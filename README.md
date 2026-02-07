# h-cli

Natural language infrastructure management via Telegram.

Send a message. Get it done.

## See it in action

### Deploy a customer lab from NetBox into EVE-NG

![Deploy customer lab](docs/gifs/deploy-lab.gif)

> "Deploy customer Acme from NetBox in EVE-NG" — pulls the topology, creates nodes, wires interfaces, lab is live.

### Scan a network and identify vendors

![Network scan](docs/gifs/network-scan.gif)

> "Scan the network and report MAC address vendors" — runs the scan, resolves OUIs, returns a formatted report.

---

## What it is

A Telegram bot backed by Claude Code. You type plain English, it executes commands in a hardened container and returns results. Session context persists for 4 hours — it remembers "that host" and "same scan again."

```
"scan 192.168.1.1"              →  nmap results in 10 seconds
"check port 443 on that host"   →  remembers which host you meant
"deploy customer X in EVE-NG"   →  pulls from NetBox, builds the lab
```

Runs on your Claude Max/Pro subscription. Zero API costs.

## How it fits your infrastructure

h-cli is the AI interface, not the security boundary. It's one half of a complete solution:

```
┌─────────────────────────────────────────────────────────────────────┐
│  h-cli (application layer)                                         │
│                                                                     │
│  Conversational interface + Asimov firewall + pattern denylist      │
│  Prevents the LLM from generating dangerous commands                │
│  Defense-in-depth — catches mistakes before they reach your infra   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Your infrastructure (trust boundary)                               │
│                                                                     │
│  Read-only TACACS/RADIUS users — can show, can't configure          │
│  Scoped API tokens — read-only NetBox, viewer-role Grafana          │
│  SSH keys with forced commands or restricted shells                  │
│  Firewall rules — h-cli's source IP can only reach allowed targets  │
└─────────────────────────────────────────────────────────────────────┘
```

**h-cli doesn't ask you to trust it. It works within the trust you've already built.**

The Asimov firewall catches LLM mistakes — a `show run` that accidentally becomes a `conf t`. Your infrastructure enforces the hard boundary — the SSH user can't `conf t` even if it tried. Together: a conversational AI interface with the same safety guarantees you already have for any other tool on your network.

Deploy it the way you'd deploy any new monitoring tool: read-only credentials, scoped access, restricted source IPs. h-cli adds intelligence on top, not risk.

## Quick Start

```bash
./install.sh                                       # creates .env + context.md, generates SSH keypair, builds
nano .env                                          # set TELEGRAM_BOT_TOKEN, ALLOWED_CHATS
nano context.md                                    # describe what YOUR deployment is for
ssh-copy-id -i ssh-keys/id_ed25519.pub user@host   # add the generated key to your servers
docker compose run -it --entrypoint bash claude-code  # one-time: shell in, run 'claude' to login
docker compose up -d
```

## Usage

**Natural language** (any plain text message):
```
scan localhost with nmap
ping 8.8.8.8
trace the route to google.com
check open ports on 192.168.1.1
deploy customer Acme from NetBox in EVE-NG
```

**Commands**:
```
/run nmap -sV 10.0.0.1    — execute a shell command directly
/new                       — clear context, start a fresh conversation
/status                    — show task queue depth
/help                      — available commands
```

Session context persists for 4 hours. Use `/new` to start fresh.

---

## Architecture

Four containers, two isolated Docker networks:

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
```

**Flow**: User sends message in Telegram → telegram-bot queues to Redis → dispatcher invokes Claude Code with session context → Claude calls `run_command()` → Asimov firewall checks the command (pattern denylist + independent Haiku gate) → core executes → result signed with HMAC → delivered back to Telegram.

Every interaction is stored as structured JSONL — conversations, commands, outputs, timestamps, session IDs.

## Security

44 security items implemented. Highlights:

- **Network isolation**: `h-network-frontend` (telegram-bot, Redis) and `h-network-backend` (core) are separate Docker networks — only claude-code bridges both
- **Fail-closed auth**: `ALLOWED_CHATS` allowlist — empty = nobody gets in
- **Non-root**: All containers run as `hcli` (uid 1000), not root
- **Capabilities**: `NET_RAW`/`NET_ADMIN` on core only; `cap_drop: ALL` + `no-new-privileges` on telegram-bot and claude-code; `read_only` rootfs on telegram-bot
- **Sudo whitelist**: only commands in `SUDO_COMMANDS` are allowed via sudo (resolved to full paths, fail-closed)
- **Asimov firewall**: MCP proxy between Claude and core. Two layers: deterministic pattern denylist (always active, zero latency) + independent Haiku gate check (on by default, resistant to conversational prompt injection)
- **HMAC-signed results**: Dispatcher signs, telegram-bot verifies. Prevents Redis result spoofing.
- **Redis auth**: password-protected, 2GB memory cap, LRU eviction, RDB + AOF persistence
- **Session chunking**: Auto-rotate at 100KB, up to 50KB of recent context injected into system prompt
- **Tool restriction**: Claude Code restricted to `mcp__h-cli-core__run_command` only
- **Pinned deps**: all Python packages pinned to major version ranges, base images pinned

Full audit trail: [SECURITY-HARDENING.md](SECURITY-HARDENING.md)

## Permissions Matrix

### Container privileges

| Container | User | Capabilities | Rootfs | Networks |
|-----------|------|-------------|--------|----------|
| `telegram-bot` | `hcli` (1000) | None (`cap_drop: ALL`) | Read-only | frontend only |
| `redis` | `redis` (default) | Default | Writable | frontend only |
| `claude-code` | `hcli` (1000) | None (`cap_drop: ALL`) | Writable | frontend + backend |
| `core` | `hcli` (1000) | `NET_RAW`, `NET_ADMIN` | Writable | backend only |

### Data access

| Container | Redis | Filesystem writes | Secrets it holds |
|-----------|-------|-------------------|------------------|
| `telegram-bot` | Read/write (task queue + results) | Logs only | `TELEGRAM_BOT_TOKEN`, `REDIS_PASSWORD`, `RESULT_HMAC_KEY` |
| `redis` | N/A (is the store) | `/data` (RDB + AOF) | `REDIS_PASSWORD` |
| `claude-code` | Read/write (tasks, sessions, memory) | Logs, session chunks, `~/.claude/` | `REDIS_PASSWORD`, `RESULT_HMAC_KEY`, Claude credentials (volume) |
| `core` | None | Logs only | SSH keys (copied at startup), integration tokens (NetBox, Grafana, EVE-NG) |

### Sudo whitelist (core only)

Commands in `SUDO_COMMANDS` are resolved to full paths at startup. Default:

```
nmap, tcpdump, traceroute, mtr, ping, ss, ip, iptables
```

Everything else is denied. Fail-closed — if a command isn't in the list, sudo refuses it.

### Optional integrations

| Integration | Container | Access | Required scope |
|-------------|-----------|--------|----------------|
| NetBox | `core` | REST API (read) | Read-only API token recommended |
| Grafana | `core` | REST API (read) | Viewer role token recommended |
| EVE-NG | `core` | REST API (read/write) | Lab user credentials |
| Ollama / vLLM | `core` | HTTP inference API | Model access only |

All integration tokens live only in core's environment. No other container sees them.

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
| `GATE_CHECK` | `true` | Asimov firewall Haiku gate check — independent model evaluates each command (adds ~2-3s per command). Set to false to disable. |
| `BLOCKED_PATTERNS` | — | Pipe-separated denylist patterns (e.g. `\| bash\|base64 -d`) |
| `BLOCKED_PATTERNS_FILE` | `/app/blocked-patterns.txt` | Pattern file (~80 patterns, 12 categories). Override with your own for external CVE/signature feeds |
| `RESULT_HMAC_KEY` | — | HMAC-SHA256 key for result signing (auto-generated by install.sh) |
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
docker compose run -it --entrypoint bash claude-code
# inside the container:
claude       # complete first-run wizard, then login when prompted
exit         # credentials are saved, exit the shell
```

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

## The Ecosystem

h-cli is part of the **h-ecosystem** — a self-improving AI ops pipeline.

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

Every conversation, shell command, and document is structured JSONL — ready for training pipelines.

### log4AI — Shell Command Logger

Drop-in shell logger that captures every command + output as structured JSONL. Supports **bash** and **zsh**. Pure shell implementation — no external dependencies (python3 optional, only for `log4ai tail` pretty-printing).

```bash
cd log4ai && ./install.sh
```

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

Sensitive commands (passwords, tokens, keys) are automatically blacklisted.

> **h-cli and log4AI are free and open source.**
> For dataset generation, training pipelines, and fine-tuning — [get in touch](#contact).

## Contact

**Want your data to train a model?**

h-cli and log4AI collect the data. The training pipeline — classification, verification, Q/A generation, fine-tuning, vector memory — is available separately.

Reach out: **[halil@hb-l.nl](mailto:halil@hb-l.nl)**

---

*h-cli is part of the h-ecosystem. Built for engineers who want their tools to learn.*
