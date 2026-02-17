"""h-cli Asimov Firewall — MCP proxy applying Asimov's Laws via a second model.

Sits between Claude (Sonnet) and core's MCP server. Every run_command
call passes through two defense layers:

  Layer 1 (deterministic): Pattern denylist — substring matching against
  known-dangerous commands. Always active, zero latency. Best-effort
  detection layer, not a hard security boundary.

  Layer 2 (Asimov gate): An independent Haiku model evaluates each command
  against groundRules.md. Sees ONLY the ground rules + the raw command —
  zero conversation context, zero session state. This makes it resistant
  to conversational prompt injection (history-based attacks cannot reach it).
  Note: the command string itself is interpolated into the gate prompt,
  so command-embedded injection is a theoretical surface (see F17/F49).

The gate defaults to ON (GATE_CHECK=true). It is the primary enforcement
layer. The denylist is a fast trip wire; the gate is the wall.
"""

import asyncio
import os
import re

from mcp.server.fastmcp import FastMCP
from mcp.client.sse import sse_client
from mcp import ClientSession

from hcli_logging import get_logger, get_audit_logger

logger = get_logger(__name__, service="firewall")
audit = get_audit_logger("firewall")

GATE_CHECK = os.environ.get("GATE_CHECK", "true").lower() == "true"
GROUND_RULES_PATH = os.environ.get("GROUND_RULES_PATH", "/app/groundRules.md")
CORE_SSE_URL = os.environ.get("CORE_SSE_URL", "http://h-cli-core:8083/sse")

# Load blocked patterns — deterministic denylist
_blocked_patterns: list[str] = []

# From env var (pipe-separated)
_raw_patterns = os.environ.get("BLOCKED_PATTERNS", "")
if _raw_patterns.strip():
    _blocked_patterns = [p.strip().lower() for p in _raw_patterns.split("|") if p.strip()]

# From file (one pattern per line) — for external CVE/signature feeds
_patterns_file = os.environ.get("BLOCKED_PATTERNS_FILE", "")
if _patterns_file:
    try:
        with open(_patterns_file) as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    _blocked_patterns.append(line)
    except FileNotFoundError:
        raise RuntimeError(f"BLOCKED_PATTERNS_FILE configured but not found: {_patterns_file}")

if _blocked_patterns:
    logger.info("Loaded %d blocked patterns", len(_blocked_patterns))

# Load ground rules once at startup
_ground_rules = ""
try:
    with open(GROUND_RULES_PATH) as f:
        _ground_rules = f.read()
except FileNotFoundError:
    if GATE_CHECK:
        raise RuntimeError(f"GATE_CHECK enabled but ground rules not found: {GROUND_RULES_PATH}")
    logger.warning("Ground rules not found at %s (gate disabled, continuing)", GROUND_RULES_PATH)

# Named h-cli-core so the tool path stays mcp__h-cli-core__run_command
# dispatcher.py and --allowedTools don't need to change
mcp = FastMCP("h-cli-core")


def _normalize_command(command: str) -> str:
    """Normalize a command string for pattern matching.

    Collapses whitespace (tabs, multiple spaces, newlines),
    strips quotes, and resolves common evasion tricks.
    """
    cmd = command.lower()
    # Strip single and double quotes
    cmd = cmd.replace('"', '').replace("'", '')
    # Collapse all whitespace (tabs, newlines, multiple spaces) to single space
    cmd = re.sub(r'\s+', ' ', cmd).strip()
    return cmd


def _pattern_check(command: str) -> tuple[bool, str]:
    """Deterministic check against blocked patterns. Zero latency, no LLM."""
    cmd_normalized = _normalize_command(command)
    for pattern in _blocked_patterns:
        if pattern in cmd_normalized:
            return False, f"DENY: blocked pattern matched — '{pattern}'"
    return True, "ALLOW: no blocked patterns matched"


async def _gate_check(command: str) -> tuple[bool, str]:
    """One-shot Haiku check against ground rules. Stateless, no context."""
    if not _ground_rules:
        return True, "ALLOW: no ground rules loaded"

    prompt = (
        "You are a security gate for a network operations tool.\n\n"
        f"RULES:\n{_ground_rules}\n\n"
        f"COMMAND TO EVALUATE:\n{command}\n\n"
        "Does this command violate any of the rules above?\n"
        "Consider: destructive operations, data loss, privilege escalation, "
        "information exfiltration, unauthorized access.\n\n"
        "Respond with EXACTLY one line:\n"
        "ALLOW: <brief reason>\n"
        "or\n"
        "DENY: <brief reason>"
    )

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--model", "haiku",
            "--tools", "", "--no-session-persistence", "--disable-slash-commands",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        response = stdout.decode().strip()

        if response.startswith("ALLOW"):
            return True, response
        elif response.startswith("DENY"):
            return False, response
        else:
            return False, f"DENY: ambiguous response — {response[:100]}"
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return False, "DENY: gate check timed out"
    except Exception as e:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
        return False, f"DENY: gate check error — {e}"


async def _forward_to_core(command: str) -> str:
    """Forward approved command to core's MCP server via SSE."""
    try:
        async with sse_client(CORE_SSE_URL) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool("run_command", {"command": command}),
                    timeout=240,
                )
                texts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                return "\n".join(texts) if texts else "(no output)"
    except asyncio.TimeoutError:
        logger.error("Core MCP timed out after 240s for command: %s", command)
        return "Error: command timed out after 240 seconds"
    except Exception as e:
        logger.exception("Failed to forward command to core")
        return f"Error: could not reach core — {e}"


@mcp.tool()
async def run_command(command: str) -> str:
    """Execute a shell command in the core container (ParrotOS).

    Available tools: nmap, dig, traceroute, mtr, tcpdump, whois,
    curl, wget, ssh, ping, iproute2, netcat, Playwright/Chromium.
    Returns combined stdout+stderr and exit code.
    """
    logger.info("Command received: %s (gate=%s)", command, GATE_CHECK)

    # Deterministic pattern denylist — always active, zero latency
    if _blocked_patterns:
        allowed, reason = _pattern_check(command)
        audit.info("pattern_check", extra={
            "command": command,
            "allowed": allowed,
            "reason": reason,
        })
        if not allowed:
            logger.warning("Pattern blocked: %s — %s", command, reason)
            return f"Command blocked by pattern denylist.\n{reason}"

    # AI gate check — optional, adds ~2-3s latency
    if GATE_CHECK:
        allowed, reason = await _gate_check(command)
        audit.info("gate_check", extra={
            "command": command,
            "allowed": allowed,
            "reason": reason,
        })
        logger.info("Gate: %s — %s", "ALLOW" if allowed else "DENY", reason)

        if not allowed:
            return f"Command blocked by security gate.\n{reason}"

    result = await _forward_to_core(command)
    audit.info("command_forwarded", extra={
        "command": command,
        "output_length": len(result),
    })

    return result


if __name__ == "__main__":
    mode = "GATED" if GATE_CHECK else "PASSTHROUGH"
    logger.info("Firewall starting (%s mode), proxying to %s", mode, CORE_SSE_URL)
    mcp.run(transport="stdio")
