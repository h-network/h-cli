# h-bot

Telegram-driven infrastructure bot. Send commands via Telegram, h-bot executes them in a hardened container with pentest tooling and reports back.

## Architecture

```
Telegram  -->  telegram-bot  -->  Redis  -->  core (ParrotOS)
                                               ├── nmap, traceroute, dig, ...
                                               ├── SSH to managed hosts
                                               └── Playwright (headless Chromium)
```

| Service | Base Image | Purpose |
|---------|-----------|---------|
| **core** | `parrotsec/core` | Command execution — network tools, SSH, browser |
| **telegram-bot** | `python:3.12-slim` | Telegram interface — receives tasks, returns results |
| **redis** | `redis:7-alpine` | Task queue between telegram-bot and core |

## Quick Start

```bash
./install.sh              # creates .env, ssh-keys/, logs/, builds images
nano .env                 # set TELEGRAM_BOT_TOKEN
cp ~/.ssh/id_ed25519* ssh-keys/   # optional: SSH keys for managed hosts
docker compose up -d
```

## Project Structure

```
h-cli/
├── core/                  # Core service (ParrotOS + tools)
│   ├── Dockerfile
│   ├── entrypoint.sh      # SSH key setup, log dir creation
│   └── requirements.txt
├── telegram-bot/          # Telegram interface service
│   ├── Dockerfile
│   ├── entrypoint.sh      # Log dir creation
│   └── requirements.txt
├── shared/                # Shared Python libraries
│   ├── setup.py
│   └── hbot_logging/      # Logging library (stdlib only)
│       ├── __init__.py    # get_logger(), get_audit_logger(), setup_logging()
│       ├── formatters.py  # PlainFormatter (pipe-delimited), AuditFormatter (JSON)
│       └── handlers.py    # RotatingFileHandler factories (10MB/5 backups)
├── logs/                  # Log output (bind-mounted into containers)
│   ├── core/              # audit.log, error.log, app.log
│   └── telegram/          # error.log, app.log
├── ssh-keys/              # SSH keys mounted into core (gitignored)
├── docker-compose.yml
├── .env.template
├── .dockerignore
└── install.sh
```

## Logging

Three log streams per service, all written to `logs/<service>/`:

| File | Format | Content |
|------|--------|---------|
| `audit.log` | JSON lines | Commands, user_id, task_id, exit_code, duration (core only) |
| `error.log` | Pipe-delimited | Exceptions, crashes, stack traces (WARNING+) |
| `app.log` | Pipe-delimited | Service lifecycle, task flow (all levels) |

Rotation: 10MB per file, 5 backups. Timestamps: UTC ISO-8601.

```python
from hbot_logging import get_logger, get_audit_logger

logger = get_logger(__name__, "core")
audit = get_audit_logger("core")

logger.info("Task received")
audit.info("", extra={"user_id": "halil", "command": "nmap -sV 10.0.0.1", "exit_code": 0})
```

## Configuration

Copy `.env.template` to `.env` and set:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (required) |
| `MCP_SERVER_PORT` | `8083` | Core MCP server port |
| `SSH_KEYS_DIR` | `./ssh-keys` | Path to SSH keys |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `MAX_CONCURRENT_TASKS` | `3` | Max parallel task executions |
| `TASK_TIMEOUT` | `300` | Task timeout in seconds |

## Security

- Core's `audit.log` is only accessible from the core container (separate bind mounts)
- SSH keys are mounted read-only, copied with strict permissions at startup
- `NET_RAW` / `NET_ADMIN` capabilities limited to core container only
- `.dockerignore` prevents secrets from leaking into build context
