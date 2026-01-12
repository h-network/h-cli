# h-cli

Natural language infrastructure management via Telegram. Part of the **h-ecosystem** for self-improving AI operations.

Send plain text messages, Claude interprets your intent, executes tools in a hardened container, remembers context across conversations, and learns from every interaction.

```
  "scan 192.168.1.1"  →  nmap results in 10 seconds
  "check port 443 on that host"  →  remembers which host you meant
  "trace the route to google.com"  →  mtr output, formatted
```

---

## The Ecosystem

```
    ┌─────────────────────────────────────────────────────────────────┐
    │                        FREE — collect                          │
    │                                                                │
    │   ┌──────────┐    ┌──────────┐    ┌──────────┐                │
    │   │  h-cli   │    │  log4AI  │    │  Docling  │               │
    │   │          │    │          │    │          │                │
    │   │ Telegram │    │  Shell   │    │ PDF to   │                │
    │   │ bot +    │    │ command  │    │ structured│               │
    │   │ sessions │    │ logger   │    │ chunks   │                │
    │   └────┬─────┘    └────┬─────┘    └────┬─────┘                │
    │        │               │               │                      │
    │        ▼               ▼               ▼                      │
    │   conversations    commands +      documents                  │
    │   as JSONL         outputs          as JSONL                  │
    │                    as JSONL                                    │
    └────────────────────────┬──────────────────────────────────────┘
                             │
                             │  daily export
                             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                     h-pipeline (overnight)                     │
    │                                                                │
    │   Classify → Verify → Generate Q/A → Verify → Fine-tune       │
    │                                         │                      │
    │                                         ├──→ Vector DB         │
    │                                         └──→ LoRA adapter      │
    └─────────────────────────────────────────────┬───────────────────┘
                                                  │
                                                  │  next morning
                                                  ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                     Your model, smarter                        │
    │                                                                │
    │   h-cli loads updated memory + optional local LLM              │
    │   Knows what you did yesterday. Learns your patterns.          │
    │   After a month: a personalized ops assistant.                 │
    └─────────────────────────────────────────────────────────────────┘
```

**Use it for a month.** Every conversation, every shell command, every document — structured, timestamped, ready for training. Wake up to a model that knows your infrastructure, your workflows, your preferences.

> **h-cli and log4AI are free and open source.**
> For dataset generation, training pipelines, and fine-tuning — [get in touch](#contact).

---

## Architecture

```
     +-----------+        +----------------------------------------------------------+
     |           |        |                    Docker Network (h-network)             |
     |  Telegram | -----> |  +-------------+    +-------+    +--------------+        |
     |           | <----- |  | telegram-bot| -> | Redis | -> | claude-code  |        |
     +-----------+        |  |             | <- |       | <- | (dispatcher) |        |
                          |  +-------------+    +-------+    +------+-------+        |
                          |                        |                |               |
                          |               session + memory    claude -p (MCP)        |
                          |               storage (JSONL)          |               |
                          |                                  +------+-------+        |
                          |                                  |     core     |        |
                          |                                  |  (ParrotOS)  |        |
                          |                                  |  MCP server  |        |
                          |                                  +--------------+        |
                          +----------------------------------------------------------+

Flow:
  1. User sends natural language message in Telegram
  2. telegram-bot queues task (with chat_id) to Redis
  3. claude-code dispatcher picks it up, looks up session for this chat
  4. Runs claude -p with --resume (existing session) or --session-id (new)
  5. Claude Code calls run_command() on core's MCP server
  6. Core executes the command, returns output
  7. Dispatcher stores session ID (4h TTL) + raw conversation in Redis
  8. telegram-bot polls result and sends back to Telegram
```

Every interaction is stored as structured JSONL — conversations, commands, outputs, timestamps, session IDs. This data accumulates and can be exported for training.

## Quick Start

```bash
./install.sh                                    # creates .env, ssh-keys/, logs/
nano .env                                       # set TELEGRAM_BOT_TOKEN, ALLOWED_CHATS
cp ~/.ssh/id_ed25519* ssh-keys/                 # optional: SSH keys for managed hosts
docker compose build
docker compose run claude-code claude login     # one-time: authenticate with Max/Pro
docker compose up -d
```

No Claude API key needed — uses your existing Max/Pro subscription. Zero API costs.

## Usage

**Natural language** (any plain text message):
```
scan localhost with nmap
ping 8.8.8.8
trace the route to google.com
check open ports on 192.168.1.1
```

**Commands**:
```
/run nmap -sV 10.0.0.1    — execute a shell command directly
/new                       — clear context, start a fresh conversation
/status                    — show task queue depth
/help                      — available commands
```

Session context persists for 4 hours. The bot remembers what "that host" or "the same scan" means. Use `/new` to start fresh.

## Free Tools Included

### log4AI — Shell Command Logger

Drop-in shell logger that captures every command + output as structured JSONL. Supports **bash** and **zsh**. Install and forget.

```bash
cd log4ai && ./install.sh
```

Detects your shell, copies the right script to `~/.log4AI/`, adds the source line to your rc file. Done.

Every command you run is logged with timestamp, hostname, working directory, exit code, duration, and full output:

```json
{
  "timestamp": "2026-02-10T14:30:00Z",
  "host": "srv-01",
  "command": "nmap -sV 192.168.1.1",
  "exit_code": 0,
  "duration_ms": 12400,
  "output": "Starting Nmap 7.94 ...",
  "cwd": "/home/ops",
  "shell": "bash"
}
```

Sensitive commands (passwords, tokens, keys) are automatically blacklisted. Use `log4ai status` to check, `log4ai off` to pause.

**Your shell history is training data.** Every day of real work is another batch of ground-truth command/output pairs.

## Data Collection

h-cli collects three streams of structured data, all JSONL:

| Source | What | Where |
|--------|------|-------|
| **Conversations** | User messages + Claude responses, tagged with chat_id | Redis (`hcli:memory:*`) |
| **Audit logs** | Commands, exit codes, durations, user IDs | `logs/*/audit.log` |
| **log4AI** | Shell commands + output from any host | `~/.log4AI/*.jsonl` |

All three formats are designed for ML pipelines. No preprocessing needed.

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
| `SESSION_TTL` | `14400` | Session context window in seconds (4h) |

### Claude Code Authentication

Uses Claude Max/Pro subscription — no API costs. One-time setup:

```bash
docker compose run claude-code claude login
```

Follow the URL to authenticate in your browser. Credentials persist in the `claude-credentials` Docker volume.

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
│   ├── dispatcher.py      # BLPOP loop → claude -p (with session resume) → result to Redis
│   ├── mcp-config.json    # MCP server config (points to core:8083)
│   └── entrypoint.sh      # Log dir creation
├── telegram-bot/          # Telegram interface service
│   ├── Dockerfile
│   ├── bot.py             # Handles natural language + /run, /new, /status, /help
│   ├── entrypoint.sh      # Log dir creation
│   └── requirements.txt
├── shared/                # Shared Python libraries
│   └── hbot_logging/      # Structured JSON logging (stdlib only)
├── log4ai/                # Shell command logger (standalone)
│   ├── log4ai.bash        # Bash logger (DEBUG trap + PROMPT_COMMAND)
│   ├── log4ai.zsh         # Zsh logger (preexec/precmd hooks)
│   └── install.sh         # Auto-detect shell, install to ~/.log4AI/
├── logs/                  # Log output (bind-mounted into containers)
├── ssh-keys/              # SSH keys mounted into core (gitignored)
├── docker-compose.yml
├── .env.template
└── install.sh
```

## Security

- `ALLOWED_CHATS` allowlist — fail-closed (empty = nobody gets in)
- SSH keys are mounted read-only, copied with strict permissions at startup
- `NET_RAW` / `NET_ADMIN` capabilities limited to core container only
- `.dockerignore` prevents secrets from leaking into build context
- Claude Code uses `--allowedTools` to restrict to MCP tools only
- log4AI auto-blacklists commands containing passwords, tokens, and secrets

## Contact

**Want your data to train a model?**

h-cli and log4AI collect the data. The training pipeline — classification, verification, Q/A generation, fine-tuning, vector memory — is available separately.

After a month of usage, you'll have enough data to fine-tune a model that knows your infrastructure, your workflows, and your patterns. Overnight batch processing, zero downtime, deployed the next morning.

Reach out: **[halil@hb-l.nl](mailto:halil@hb-l.nl)**

---

*h-cli is part of the h-ecosystem. Built for engineers who want their tools to learn.*
