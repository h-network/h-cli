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
     |           |        |                                                          |
     |  Telegram | -----> |  +-------------+    +-------+    +--------------+        |
     |           | <----- |  | telegram-bot| -> | Redis | -> | claude-code  |        |
     +-----------+        |  |             | <- |       | <- | (dispatcher) |        |
                          |  +-------------+    +-------+    +------+-------+        |
                          |   h-network-frontend              |  both networks |
                          |                            claude -p (MCP)         |
                          |               session + memory         |           |
                          |               storage (JSONL)    +-----+------+    |
                          |                                  | firewall   |    |
                          |                                  | (MCP proxy)|    |
                          |                                  +-----+------+    |
                          |                                        |           |
                          |                                  +-----+------+    |
                          |                                  |    core    |    |
                          |                                  | (ParrotOS) |    |
                          |                                  |  MCP server|    |
                          |                                  +------------+    |
                          |                                h-network-backend   |
                          +----------------------------------------------------------+

Flow:
  1. User sends natural language message in Telegram
  2. telegram-bot queues task (with chat_id) to Redis
  3. claude-code dispatcher picks it up, looks up session for this chat
  4. Builds system prompt from groundRules.md + context.md + session chunks
  5. Runs claude -p with --resume (existing session) or --session-id (new)
  6. Claude Code calls run_command() — routed through firewall.py (MCP proxy)
  7. Firewall runs pattern denylist check, then optional Haiku gate check
  8. If allowed, firewall forwards to core's MCP server via SSE
  9. Core executes the command, returns output
  10. Dispatcher stores session ID (4h TTL) + raw conversation in Redis
  11. Session chunking: when accumulated size > 100KB, history is dumped to disk
  12. telegram-bot polls result and sends back to Telegram
```

Every interaction is stored as structured JSONL — conversations, commands, outputs, timestamps, session IDs. This data accumulates and can be exported for training.

## Quick Start

```bash
./install.sh                                    # creates .env + context.md, generates SSH keypair, builds
nano .env                                       # set TELEGRAM_BOT_TOKEN, ALLOWED_CHATS
nano context.md                                 # describe what YOUR deployment is for
ssh-copy-id -i ssh-keys/id_ed25519.pub user@host  # add the generated key to your servers
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
| `REDIS_PASSWORD` | — | Redis authentication password (required) |
| `SSH_KEYS_DIR` | `./ssh-keys` | Path to SSH keys |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `MAX_CONCURRENT_TASKS` | `3` | Max parallel task executions |
| `TASK_TIMEOUT` | `300` | Task timeout in seconds |
| `SESSION_TTL` | `14400` | Session context window in seconds (4h) |
| `SUDO_COMMANDS` | `nmap,tcpdump,...` | Comma-separated commands hcli can sudo (full paths resolved at startup) |
| `GATE_CHECK` | `false` | Enable Asimov firewall Haiku gate check (adds ~2-3s per command) |
| `BLOCKED_PATTERNS` | — | Pipe-separated denylist patterns (e.g. `\| bash\|base64 -d`) |
| `BLOCKED_PATTERNS_FILE` | `/app/blocked-patterns.txt` | Pattern file (~80 patterns, 12 categories). Override with your own for external CVE/signature feeds |
| `NETBOX_URL` | — | NetBox instance URL (optional) |
| `NETBOX_API_TOKEN` | — | NetBox API token (optional) |
| `GRAFANA_URL` | — | Grafana instance URL (optional) |
| `GRAFANA_API_TOKEN` | — | Grafana API token (optional) |
| `EVE_NG_URL` | — | EVE-NG REST API URL (optional) |
| `EVE_NG_USERNAME` | — | EVE-NG username (optional) |
| `EVE_NG_PASSWORD` | — | EVE-NG password (optional) |
| `OLLAMA_URL` | — | Ollama API URL (optional) |
| `OLLAMA_MODEL` | — | Ollama model name (optional) |
| `VLLM_URL` | — | vLLM API URL (optional) |
| `VLLM_API_KEY` | — | vLLM API key (optional) |
| `VLLM_MODEL` | — | vLLM model name (optional) |

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
│   ├── entrypoint.sh      # SSH key setup, sudo whitelist, log dir creation
│   └── requirements.txt
├── claude-code/           # Claude Code dispatcher service
│   ├── Dockerfile         # Ubuntu + Node.js + Claude Code CLI + Python
│   ├── dispatcher.py      # BLPOP loop → claude -p (with session resume + chunking) → result to Redis
│   ├── firewall.py        # Asimov firewall — MCP proxy with pattern denylist + Haiku gate
│   ├── mcp-config.json    # MCP server config (points to firewall proxy, not core directly)
│   ├── CLAUDE.md          # Bot context — tool restrictions + session chunking
│   └── entrypoint.sh      # Log dir creation
├── telegram-bot/          # Telegram interface service
│   ├── Dockerfile
│   ├── bot.py             # Handles natural language + /run, /new, /status, /help
│   ├── entrypoint.sh      # Log dir creation
│   └── requirements.txt
├── shared/                # Shared Python libraries
│   └── hcli_logging/      # Structured JSON logging (stdlib only)
├── log4ai/                # Shell command logger (standalone)
│   ├── log4ai.bash        # Bash logger (DEBUG trap + PROMPT_COMMAND)
│   ├── log4ai.zsh         # Zsh logger (preexec/precmd hooks)
│   └── install.sh         # Auto-detect shell, install to ~/.log4AI/
├── logs/                  # Log output (bind-mounted into containers)
├── ssh-keys/              # SSH keys mounted into core (gitignored)
├── docker-compose.yml
├── blocked-patterns.txt      # Default denylist (~80 patterns, 12 categories)
├── groundRules.md            # Universal safety rules (ships with h-cli)
├── context.md.template       # Example context — copy to context.md
├── context.md                # YOUR deployment context (gitignored)
├── .env.template
└── install.sh
```

## Security

- **Network isolation**: `h-network-frontend` (telegram-bot, Redis) and `h-network-backend` (core) are separate Docker networks — only claude-code bridges both
- **Fail-closed auth**: `ALLOWED_CHATS` allowlist — empty = nobody gets in
- **SSH keys**: mounted read-only, copied to `/home/hcli/.ssh/` with strict permissions at startup
- **Sudo whitelist**: only commands listed in `SUDO_COMMANDS` are allowed via sudo (resolved to full paths, fail-closed)
- **Capabilities**: `NET_RAW`/`NET_ADMIN` on core only; `cap_drop: ALL` + `no-new-privileges` + `read_only` rootfs on telegram-bot and claude-code
- **Redis auth**: password-protected via `REDIS_PASSWORD`, generated into config at runtime (not visible in `ps`)
- **Health checks**: all services have Docker healthchecks (MCP endpoint, Redis ping, Redis connectivity)
- **Graceful shutdown**: dispatcher handles SIGTERM, finishes current task before exiting
- **Input validation**: malformed JSON payloads skipped, invalid ALLOWED_CHATS entries logged and ignored
- **Redis limits**: 2GB memory cap with LRU eviction, RDB + AOF persistence (no data loss on reboot)
- **Pinned deps**: all Python packages pinned to major version ranges, no surprise breakage on rebuild
- **Tool restriction**: Claude Code uses `--allowedTools` to restrict to `mcp__h-cli-core__run_command` only
- **Asimov firewall**: MCP proxy (`firewall.py`) between Sonnet and core. Two layers: deterministic pattern denylist (always active, zero latency, supports external signature files) + independent Haiku gate check (optional, ~2-3s, immune to prompt injection)
- **Session chunking**: Sessions auto-rotate at 100KB, history dumped to disk, up to 50KB of recent context injected into system prompt
- **Build context**: `.dockerignore` prevents secrets from leaking into images
- **log4AI**: auto-blacklists commands containing passwords, tokens, and secrets

## Contact

**Want your data to train a model?**

h-cli and log4AI collect the data. The training pipeline — classification, verification, Q/A generation, fine-tuning, vector memory — is available separately.

After a month of usage, you'll have enough data to fine-tune a model that knows your infrastructure, your workflows, and your patterns. Overnight batch processing, zero downtime, deployed the next morning.

Reach out: **[halil@hb-l.nl](mailto:halil@hb-l.nl)**

---

*h-cli is part of the h-ecosystem. Built for engineers who want their tools to learn.*
