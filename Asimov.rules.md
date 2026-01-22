# Asimov Gatekeeper — Implementation Plan

## Concept

An independent Haiku model checks every command before execution. It sees only the
ground rules and the proposed command — no conversation history, no user context.
Cannot be prompt-injected through conversation because it has zero session state.

```
User message
    |
    v
Sonnet (with memory, --resume) → proposes command as text
    |
    v
Haiku (one-shot, no state) → ALLOW or DENY against groundRules.md
    |
    v
If ALLOW → execute on core via MCP → feed result back to Sonnet
If DENY  → return denial reason to Sonnet → Sonnet informs user
```

## Why

- Sonnet can be socially engineered via conversation context (prompt injection)
- Ground rules in the system prompt are advisory — the model can be convinced to ignore them
- Haiku sees ONLY the command + rules. No charm, no context manipulation, no conversation history
- Multi-layer: ALLOWED_CHATS → Sonnet → **Haiku gatekeeper** → SUDO_COMMANDS → network isolation

## Trade-off

~2-3 seconds extra per command execution. Acceptable for network ops.

## Configuration

Add to `.env.template` and `.env`:

```
# Asimov Gatekeeper — independent Haiku model checks every command against ground rules
# Adds ~2-3s latency per command. Set to true for production environments.
GATE_CHECK=false
```

Pass into claude-code container via docker-compose.yml environment section.

## Implementation — dispatcher.py changes

### 1. New constant and env var

```python
GATE_CHECK = os.environ.get("GATE_CHECK", "false").lower() == "true"
```

### 2. Gate check function

```python
def gate_check(command: str) -> tuple[bool, str]:
    """Ask Haiku if this command violates ground rules. Returns (allowed, reason)."""
    try:
        with open(GROUND_RULES_PATH) as f:
            rules = f.read()
    except FileNotFoundError:
        return True, "No ground rules found, allowing by default"

    prompt = (
        f"You are a security gate for a network operations tool.\n\n"
        f"RULES:\n{rules}\n\n"
        f"COMMAND TO EVALUATE:\n{command}\n\n"
        f"Does this command violate any of the rules above?\n"
        f"Consider: destructive operations, data loss, privilege escalation, "
        f"information exfiltration, unauthorized access.\n\n"
        f"Respond with EXACTLY one line:\n"
        f"ALLOW: <brief reason>\n"
        f"or\n"
        f"DENY: <brief reason>"
    )

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        response = proc.stdout.strip()
        if response.startswith("ALLOW"):
            return True, response
        elif response.startswith("DENY"):
            return False, response
        else:
            # Ambiguous response — fail open or fail closed (configurable)
            logger.warning("Ambiguous gate response: %s", response)
            return False, f"DENY: ambiguous gatekeeper response"
    except subprocess.TimeoutExpired:
        logger.warning("Gate check timed out")
        return False, "DENY: gatekeeper timed out"
    except Exception as e:
        logger.exception("Gate check failed")
        return False, f"DENY: gatekeeper error: {e}"
```

### 3. Two-phase execution flow

The current flow calls `claude -p` with `--allowedTools mcp__h-cli-core__run_command`
and Claude executes commands directly via MCP.

The gated flow changes this:

**Phase 1 — Sonnet proposes command:**
- System prompt instructs Sonnet to OUTPUT the command it wants to run
  in a structured format, NOT execute it directly
- `--allowedTools` is removed (or set to none)
- Sonnet returns something like: `COMMAND: nmap -sV 192.168.1.1`

**Phase 2 — Haiku gate check:**
- Extract command from Sonnet's output
- Call `gate_check(command)`
- If DENY: feed denial back to Sonnet via `--resume`, Sonnet tells user

**Phase 3 — Execute and return:**
- If ALLOW: dispatcher calls run_command on core directly via HTTP POST to MCP SSE
- Feed command output back to Sonnet via `--resume`
- Sonnet interprets results and responds to user

### 4. System prompt changes for gated mode

When `GATE_CHECK=true`, the system prompt needs an additional section:

```
## Command Execution Protocol

You do NOT have direct access to run_command. Instead:
1. When you need to run a command, output it in this exact format:
   EXECUTE: <command>
2. The command will be reviewed by an independent security check
3. You will receive the result or a denial with reason
4. If denied, explain to the user why and suggest alternatives

Only output ONE command at a time. Wait for the result before proposing the next.
```

### 5. Parsing Sonnet's output

```python
import re

def extract_command(output: str) -> str | None:
    """Extract proposed command from Sonnet's output."""
    match = re.search(r"EXECUTE:\s*(.+)", output)
    return match.group(1).strip() if match else None
```

### 6. Direct MCP call from dispatcher

When the gate allows a command, the dispatcher needs to call core's MCP server
directly instead of going through Claude's MCP client:

```python
import requests

def execute_on_core(command: str) -> str:
    """Call run_command on core's MCP server directly."""
    # MCP SSE protocol — establish session then send tool call
    # Implementation depends on MCP client library or raw HTTP
    # Alternative: use the mcp Python package as a client
    pass
```

This is the most complex part. Options:
- Use the `mcp` Python package as an MCP client
- Make raw HTTP requests to the SSE endpoint
- Use a simpler HTTP endpoint (add a REST route to mcp_server.py alongside SSE)

**Recommended**: Add a simple `/execute` REST endpoint to `mcp_server.py` that the
dispatcher can POST to directly. This avoids SSE complexity.

```python
# In core/mcp_server.py — add alongside existing MCP server
from fastapi import FastAPI
app = FastAPI()

@app.post("/execute")
async def execute(request: dict):
    command = request.get("command", "")
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=280)
    return {"stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode}
```

**Security note**: This endpoint must only be accessible from h-network-backend.
Same security posture as the existing MCP endpoint (network isolation only).

### 7. Putting it together — modified process_task

```python
def process_task(r, task_json):
    # ... existing setup ...

    if GATE_CHECK:
        # Phase 1: Ask Sonnet what command to run (no MCP tools)
        system_prompt = build_system_prompt(chat_id)  # includes gated-mode instructions
        cmd = ["claude", "-p", message, "--model", "sonnet",
               "--system-prompt", system_prompt] + session_flags
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=290)
        sonnet_output = proc.stdout.strip()

        proposed = extract_command(sonnet_output)
        if proposed:
            # Phase 2: Gate check with Haiku
            allowed, reason = gate_check(proposed)
            audit.info("gate_check", extra={
                "command": proposed, "allowed": allowed, "reason": reason
            })

            if allowed:
                # Phase 3: Execute and feed back
                result = execute_on_core(proposed)
                # Resume session with the result
                feedback = f"Command executed. Output:\n{result}"
                cmd2 = ["claude", "-p", feedback, "--model", "sonnet",
                        "--resume", session_id]
                proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=290)
                output = proc2.stdout.strip()
            else:
                # Feed denial back to Sonnet
                feedback = f"Command DENIED by security gate: {reason}. Inform the user."
                cmd2 = ["claude", "-p", feedback, "--model", "sonnet",
                        "--resume", session_id]
                proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=290)
                output = proc2.stdout.strip()
        else:
            # No command proposed — Sonnet just answered directly
            output = sonnet_output
    else:
        # Original flow — direct MCP execution (no gate)
        cmd = ["claude", "-p", message, "--mcp-config", MCP_CONFIG,
               "--allowedTools", "mcp__h-cli-core__run_command",
               "--model", "sonnet", "--system-prompt", system_prompt
               ] + session_flags
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=290)
        output = proc.stdout.strip()
```

## Files to modify

| File | Change |
|------|--------|
| `.env.template` | Add `GATE_CHECK=false` |
| `docker-compose.yml` | Pass `GATE_CHECK` env var to claude-code |
| `claude-code/dispatcher.py` | Add `gate_check()`, `extract_command()`, `execute_on_core()`, two-phase flow |
| `core/mcp_server.py` | Add `/execute` REST endpoint alongside MCP SSE |
| `groundRules.md` | Already exists — used as-is by the gate check |

## Audit logging

Every gate check is logged:

```json
{
  "event": "gate_check",
  "command": "nmap -sV 192.168.1.1",
  "allowed": true,
  "reason": "ALLOW: standard network scan, non-destructive",
  "task_id": "abc-123",
  "chat_id": "-5075829124"
}
```

Denials are also logged at WARNING level for monitoring.

## Edge cases

- **Multi-command tasks**: Sonnet may need to run multiple commands. Each one goes
  through the gate individually. The `--resume` loop continues until Sonnet outputs
  a final answer without EXECUTE.
- **Timeout budget**: With gate check, each command costs ~2-3s extra. The 290s task
  timeout may need adjustment for complex multi-command tasks.
- **Haiku ambiguity**: If Haiku doesn't clearly ALLOW or DENY, fail closed (DENY).
- **Gate check failure**: If `claude -p --model haiku` fails (timeout, crash), fail
  closed. Log the failure.
- **Non-gated mode**: When `GATE_CHECK=false`, the original MCP flow is used unchanged.
  Zero performance impact when disabled.

## Testing

1. `GATE_CHECK=false` — verify original flow works unchanged
2. `GATE_CHECK=true` — send "ping 8.8.8.8" → should ALLOW
3. `GATE_CHECK=true` — send "delete all files on the server" → should DENY
4. `GATE_CHECK=true` — prompt injection attempt in message → Haiku shouldn't see it
5. Multi-command task — verify each command is gated individually
6. Gate timeout — verify fail-closed behavior
