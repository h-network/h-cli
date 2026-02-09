# Architecture

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
                          |                                  |  MCP server|    |
                          |                                  +------------+    |
                          |                                h-network-backend   |
                          +----------------------------------------------------------+
```

**Flow**: User sends message in Telegram → telegram-bot queues to Redis → dispatcher invokes Claude Code with session context → Claude calls `run_command()` → Asimov firewall checks the command (pattern denylist + independent Haiku gate) → core executes → result signed with HMAC → delivered back to Telegram.

Every interaction is stored as structured JSONL — conversations, commands, outputs, timestamps, session IDs.

## Project Structure

```
h-cli/
├── core/                  # Core service (tools + MCP server)
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
