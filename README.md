# h-cli

Telegram-driven infrastructure CLI. Send commands via Telegram, h-cli executes them in a hardened container with pentest tooling and reports back.

## Architecture

```
                          +--------------------------------------------------+
                          |                  Docker Network                   |
     +-----------+        |                                                  |
     |           |        |  +-------------+    +-------+    +------------+  |
     |  Telegram | -----> |  | telegram-bot| -> | Redis | -> |    core    |  |
     |           | <----- |  |             | <- |       | <- | (ParrotOS) |  |
     +-----------+        |  +------+------+    +-------+    +-----+------+  |
                          |         |                              |         |
                          +---------|------------------------------|----------+
                                    |                              |
                    +---------------+--------+     +---------------+--------+
                    |     hbot_logging       |     |     hbot_logging       |
                    |     (shared lib)       |     |     (shared lib)       |
                    +--------+-------+-------+     +---+----+-------+------+
                             |       |                 |    |       |
                             v       v                 v    v       v
                          app.log error.log      audit.log app.log error.log
                          ~~~~~~~~~~~~~~~~~~~~~~~  ~~~~~~~~~~~~~~~~~~~~~~~~
                          logs/telegram/           logs/core/
                          (bind mount)             (bind mount)

     +------------------+----------------------------------------------+
     |   shared/        |  Installed in both containers via pip        |
     |   hbot_logging/  |  stdlib only -- no external dependencies     |
     |                  |                                              |
     |   formatters.py  |  PlainFormatter    -> pipe-delimited text    |
     |                  |  AuditFormatter    -> JSON lines             |
     |   handlers.py    |  RotatingFileHandler (10MB x 5 backups)      |
     |   __init__.py    |  get_logger()  get_audit_logger()            |
     +------------------+----------------------------------------------+

     +------------------+----------------------------------------------+
     |   core/          |  ParrotOS + pentest tools                    |
     |                  |  nmap, dig, traceroute, mtr, tcpdump, whois  |
     |                  |  SSH client + managed host keys              |
     |                  |  Playwright + headless Chromium              |
     +------------------+----------------------------------------------+

     +------------------+----------------------------------------------+
     |   telegram-bot/  |  Python 3.12 slim                           |
     |                  |  Telegram API interface                      |
     |                  |  Task dispatch + result delivery             |
     +------------------+----------------------------------------------+

     +------------------+----------------------------------------------+
     |   redis          |  Redis 7 Alpine                              |
     |                  |  Task queue + pub/sub between services       |
     +------------------+----------------------------------------------+
```

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

### Dataset Generation

Audit and error logs are structured for direct use as ML/LLM training datasets. `audit.log` is already valid JSONL — each line is a self-contained record ready for fine-tuning pipelines:

```jsonl
{"timestamp": "2026-02-10T13:27:03Z", "level": "INFO", "user_id": "halil", "command": "nmap -sV 10.0.0.1", "exit_code": 0, "duration": 12.4}
{"timestamp": "2026-02-10T13:28:15Z", "level": "INFO", "user_id": "halil", "command": "dig AXFR example.com", "exit_code": 1, "duration": 3.1}
```

This enables training models on command success/failure patterns, expected runtimes, workflow sequences (grouped by `task_id`), and anomaly detection. Logs accumulate naturally during normal usage — no separate data collection step needed.

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
