# h-cli

Telegram-driven infrastructure CLI with natural language support. Send plain text messages via Telegram, Claude Code interprets your intent, executes the right tools in a hardened ParrotOS container, and reports back.

## Architecture

```
     +-----------+        +----------------------------------------------------------+
     |           |        |                    Docker Network                        |
     |  Telegram | -----> |  +-------------+    +-------+    +--------------+        |
     |           | <----- |  | telegram-bot| -> | Redis | -> | claude-code  |        |
     +-----------+        |  |             | <- |       | <- | (dispatcher) |        |
                          |  +-------------+    +-------+    +------+-------+        |
                          |                                         |               |
                          |                                   claude -p (MCP)        |
                          |                                         |               |
                          |                                  +------+-------+        |
                          |                                  |     core     |        |
                          |                                  |  (ParrotOS)  |        |
                          |                                  |  MCP server  |        |
                          |                                  |  port 8083   |        |
                          |                                  +--------------+        |
                          +----------------------------------------------------------+

Flow:
  1. User sends natural language message in Telegram
  2. telegram-bot queues task to Redis
  3. claude-code dispatcher picks it up, runs claude -p with MCP config
  4. Claude Code calls run_command() on core's MCP server
  5. Core executes the command, returns output
  6. Claude Code formats the result, dispatcher stores in Redis
  7. telegram-bot polls result and sends back to Telegram
```

## Quick Start

```bash
./install.sh                                    # creates .env, ssh-keys/, logs/
nano .env                                       # set TELEGRAM_BOT_TOKEN, ALLOWED_CHATS
cp ~/.ssh/id_ed25519* ssh-keys/                 # optional: SSH keys for managed hosts
docker compose build
docker compose run claude-code claude login     # one-time: authenticate with Max/Pro
docker compose up -d
```

## Project Structure

```
h-cli/
├── core/                  # Core service (ParrotOS + tools + MCP server)
│   ├── Dockerfile
│   ├── mcp_server.py      # FastMCP SSE server exposing run_command tool
│   ├── entrypoint.sh      # SSH key setup, log dir creation
│   └── requirements.txt
├── claude-code/           # Claude Code dispatcher service
│   ├── Dockerfile         # Ubuntu + Node.js + Claude Code CLI + Python
│   ├── dispatcher.py      # BLPOP loop → claude -p → result to Redis
│   ├── mcp-config.json    # MCP server config (points to core:8083)
│   └── entrypoint.sh      # Log dir creation
├── telegram-bot/          # Telegram interface service
│   ├── Dockerfile
│   ├── bot.py             # Handles natural language + /run, /status, /help
│   ├── entrypoint.sh      # Log dir creation
│   └── requirements.txt
├── shared/                # Shared Python libraries
│   ├── setup.py
│   └── hbot_logging/      # Logging library (stdlib only)
│       ├── __init__.py    # get_logger(), get_audit_logger(), setup_logging()
│       ├── formatters.py  # AppFormatter, AuditFormatter (both JSON lines)
│       └── handlers.py    # RotatingFileHandler factories (10MB x 5 backups)
├── logs/                  # Log output (bind-mounted into containers)
│   ├── core/              # audit.log, error.log, app.log
│   ├── claude/            # audit.log, error.log, app.log
│   └── telegram/          # audit.log, error.log, app.log
├── ssh-keys/              # SSH keys mounted into core (gitignored)
├── docker-compose.yml
├── .env.template
├── .dockerignore
└── install.sh
```

## Usage

**Natural language** (any plain text message):
```
scan localhost with nmap
ping 8.8.8.8
trace the route to google.com
check open ports on 192.168.1.1
```

**Direct commands**:
```
/run nmap -sV 10.0.0.1
/status
/help
```

## Logging

Three log streams per service, all written to `logs/<service>/`:

| File | Format | Content |
|------|--------|---------|
| `audit.log` | JSON lines | Commands, user_id, task_id, exit_code, duration |
| `error.log` | JSON lines | Exceptions, crashes, stack traces (WARNING+) |
| `app.log` | JSON lines | Service lifecycle, task flow (all levels) |

Rotation: 10MB per file, 5 backups. Timestamps: UTC ISO-8601.

### Dataset Generation

All three log streams output valid JSONL — each line is a self-contained JSON record ready for ML/LLM fine-tuning pipelines:

```jsonl
# audit.log — structured command outcomes
{"timestamp": "2026-02-10T13:27:03Z", "level": "INFO", "user_id": "halil", "command": "nmap -sV 10.0.0.1", "exit_code": 0, "duration": 12.4}

# error.log — exceptions with full tracebacks
{"timestamp": "2026-02-10T13:28:15Z", "level": "ERROR", "logger": "core.executor", "message": "Command failed", "exception": "TimeoutError: ..."}

# app.log — operational events
{"timestamp": "2026-02-10T13:27:01Z", "level": "INFO", "logger": "core.executor", "message": "Task t-001 received"}
```

## Configuration

Copy `.env.template` to `.env` and set:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (required) |
| `ALLOWED_CHATS` | — | Comma-separated Telegram chat IDs (required) |
| `SSH_KEYS_DIR` | `./ssh-keys` | Path to SSH keys |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `MAX_CONCURRENT_TASKS` | `3` | Max parallel task executions |
| `TASK_TIMEOUT` | `300` | Task timeout in seconds |

### Claude Code Authentication

Uses Claude Max/Pro subscription — no API costs. One-time setup:

```bash
docker compose run claude-code claude login
```

Follow the URL to authenticate in your browser. Credentials persist in the `claude-credentials` Docker volume.

## Security

- `ALLOWED_CHATS` allowlist — fail-closed (empty = nobody gets in)
- Core's `audit.log` is only accessible from the core container (separate bind mounts)
- SSH keys are mounted read-only, copied with strict permissions at startup
- `NET_RAW` / `NET_ADMIN` capabilities limited to core container only
- `.dockerignore` prevents secrets from leaking into build context
- Claude Code uses `--allowedTools` to restrict to MCP tools only
