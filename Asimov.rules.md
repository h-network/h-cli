# Asimov Firewall — Implementation

## Concept

An MCP proxy (`firewall.py`) sits between Claude (Sonnet) and core's MCP server.
Every `run_command` call passes through the firewall transparently. When
`GATE_CHECK=true`, an independent Haiku model checks each command against
`groundRules.md` before forwarding.

Haiku sees ONLY the ground rules + the command — no conversation history,
no user context. Cannot be prompt-injected through conversation because it
has zero session state.

```
Sonnet calls run_command("nmap -sV 192.168.1.1")
    |
    v
firewall.py (stdio MCP server, inside claude-code container)
    |
    ├── GATE_CHECK=false → forward directly to core
    |
    └── GATE_CHECK=true
            |
            v
        Haiku (one-shot, no state) → ALLOW or DENY against groundRules.md
            |
            ├── ALLOW → forward to core via MCP SSE
            └── DENY  → return denial to Sonnet → Sonnet informs user
```

## Why This Design

- **Invisible to Sonnet**: Sonnet calls `run_command` normally — doesn't know the
  firewall exists. Can't be convinced to bypass it via prompt injection.
- **Infrastructure-level**: Like a network firewall — inspects every "packet"
  (command) against a ruleset, application has no awareness or control.
- **Module change only**: dispatcher.py untouched, core untouched. Drop-in proxy.
- **Zero overhead when disabled**: `GATE_CHECK=false` = direct passthrough to core.
- **Multi-layer security**: ALLOWED_CHATS → Sonnet → **Haiku firewall** →
  SUDO_COMMANDS → network isolation → container hardening.

## Trade-off

~2-3 seconds extra per command execution. Acceptable for network ops.

## Configuration

In `.env`:

```
# Asimov Firewall — independent Haiku model checks every command against ground rules
# Adds ~2-3s latency per command. Set to true for production environments.
GATE_CHECK=false
```

Passed into claude-code container via docker-compose.yml environment section.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      claude-code container                          │
│                                                                     │
│  dispatcher.py                                                      │
│       │                                                             │
│       └── claude -p --mcp-config mcp-config.json                    │
│               │                                                     │
│               └── mcp-config.json → stdio → firewall.py             │
│                       │                                             │
│                       ├── if GATE_CHECK=true:                       │
│                       │       claude -p --model haiku (one-shot)    │
│                       │       └── ALLOW or DENY                     │
│                       │                                             │
│                       └── forward to core:8083/sse (MCP client)     │
│                                                                     │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ h-network-backend
                                   v
                            ┌──────────────┐
                            │     core     │
                            │  (ParrotOS)  │
                            │  MCP server  │
                            └──────────────┘
```

## Files Changed

| File | Change |
|------|--------|
| `claude-code/firewall.py` | **New** — stdio MCP proxy with gate check |
| `claude-code/mcp-config.json` | Changed from SSE→core to stdio→firewall.py |
| `claude-code/Dockerfile` | Added `mcp` pip dependency, copy firewall.py |
| `claude-code/entrypoint.sh` | Added firewall log directory |
| `docker-compose.yml` | Pass `GATE_CHECK` env, add firewall log volume |
| `.env.template` | Added `GATE_CHECK=false` |

Files **NOT** changed: `dispatcher.py`, `core/mcp_server.py`, `telegram-bot/bot.py`

## How It Works

### 1. MCP Proxy (firewall.py)

firewall.py is a stdio MCP server named `h-cli-core`. This means the tool path
stays `mcp__h-cli-core__run_command` — dispatcher.py's `--allowedTools` doesn't
need to change.

When Sonnet calls `run_command(command)`:

1. firewall.py receives the call
2. If `GATE_CHECK=true`: runs `_gate_check(command)`
3. If allowed (or `GATE_CHECK=false`): forwards to core via `_forward_to_core(command)`
4. Returns result to Sonnet

### 2. Gate Check (_gate_check)

```python
claude -p "<rules + command>" --model haiku
```

- One-shot, stateless — new process per check
- Sees ONLY groundRules.md + the command
- Returns `ALLOW: <reason>` or `DENY: <reason>`
- Ambiguous or failed responses → fail closed (DENY)
- 30 second timeout

### 3. Core Forwarding (_forward_to_core)

Uses the `mcp` Python SDK as an SSE client to call core's MCP server at
`http://h-cli-core:8083/sse`. Calls `run_command` tool, returns text result.

## Audit Logging

Every gate check is logged to `/var/log/hcli/firewall/audit.log`:

```json
{
  "timestamp": "2026-02-12T10:30:00Z",
  "event": "gate_check",
  "command": "nmap -sV 192.168.1.1",
  "allowed": true,
  "reason": "ALLOW: standard network scan, non-destructive"
}
```

Denials are also logged at WARNING level in `app.log`.

## Edge Cases

- **Multi-command tasks**: Sonnet may call `run_command` multiple times. Each call
  goes through the firewall individually — each is gated independently.
- **Gate check failure**: If `claude -p --model haiku` fails (timeout, crash),
  fail closed (DENY). Logged as error.
- **Core unreachable**: If the MCP SSE connection to core fails, error returned
  to Sonnet. Sonnet informs the user.
- **No ground rules**: If groundRules.md is missing, all commands are allowed
  (with a warning log). The firewall still proxies to core.
- **Non-gated mode**: When `GATE_CHECK=false`, commands pass straight through.
  No Haiku invocation, minimal overhead (MCP proxy only).

## Testing

1. `GATE_CHECK=false` — verify commands pass through to core normally
2. `GATE_CHECK=true` — send "ping 8.8.8.8" → should ALLOW
3. `GATE_CHECK=true` — send "rm -rf /" → should DENY
4. `GATE_CHECK=true` — prompt injection in Telegram message → Haiku doesn't see it
5. Multi-command task — verify each command is gated individually
6. Gate timeout — verify fail-closed behavior
7. Core down — verify error is returned gracefully
