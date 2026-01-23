"""h-cli Firewall — MCP proxy with optional Haiku gate check.

Sits between Claude (Sonnet) and core's MCP server. Every run_command
call passes through here. When GATE_CHECK=true, an independent Haiku
model checks each command against groundRules.md before forwarding.

Haiku sees ONLY the ground rules + the command — zero conversation
context, zero session state. Cannot be prompt-injected.
"""

import asyncio
import os

from mcp.server.fastmcp import FastMCP
from mcp.client.sse import sse_client
from mcp import ClientSession

from hcli_logging import get_logger, get_audit_logger

logger = get_logger(__name__, service="firewall")
audit = get_audit_logger("firewall")

GATE_CHECK = os.environ.get("GATE_CHECK", "false").lower() == "true"
GROUND_RULES_PATH = os.environ.get("GROUND_RULES_PATH", "/app/groundRules.md")
CORE_SSE_URL = os.environ.get("CORE_SSE_URL", "http://h-cli-core:8083/sse")

# Load ground rules once at startup
_ground_rules = ""
try:
    with open(GROUND_RULES_PATH) as f:
        _ground_rules = f.read()
except FileNotFoundError:
    logger.warning("Ground rules not found at %s", GROUND_RULES_PATH)

# Named h-cli-core so the tool path stays mcp__h-cli-core__run_command
# dispatcher.py and --allowedTools don't need to change
mcp = FastMCP("h-cli-core")


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

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt, "--model", "haiku",
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
        return False, "DENY: gate check timed out"
    except Exception as e:
        return False, f"DENY: gate check error — {e}"


async def _forward_to_core(command: str) -> str:
    """Forward approved command to core's MCP server via SSE."""
    try:
        async with sse_client(CORE_SSE_URL) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result = await session.call_tool(
                    "run_command", {"command": command},
                )
                texts = []
                for block in result.content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                return "\n".join(texts) if texts else "(no output)"
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
