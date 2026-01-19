# h-cli Project Status

> Written: 2026-02-10 | Git: `cc1e04a` on `main` | 35 commits

## What This Is

Natural language infrastructure management via Telegram. User sends a message, Claude Code interprets it, executes commands in a hardened ParrotOS container, returns results. Session context persists for 4 hours.

Part of the **h-ecosystem** — a self-improving AI ops pipeline where h-cli collects structured interaction data (JSONL) that feeds into training pipelines downstream.

## Architecture

Four containers, two isolated Docker networks:

```
Telegram --> telegram-bot --> Redis --> claude-code --> core (ParrotOS)
             h-network-frontend          bridges both    h-network-backend
```

| Container | Image | Role |
|-----------|-------|------|
| `h-cli-telegram` | `python:3.12-slim` | Telegram polling bot, auth gatekeeper, queues tasks to Redis |
| `h-cli-redis` | `redis:7-alpine` | Message queue (`hcli:tasks`), result store, session storage |
| `h-cli-claude` | `ubuntu:24.04` + Node 22 + Claude Code CLI | BLPOP dispatcher, invokes `claude -p` with MCP + session resume |
| `h-cli-core` | `parrotsec/core:latest` | FastMCP SSE server exposing `run_command()`, nmap/dig/mtr/ssh/Playwright |

Claude Code uses the user's Max/Pro subscription (zero API cost). Auth via `docker compose run claude-code claude login`, credentials persist in a Docker volume.

## Key Files

| File | Purpose |
|------|---------|
| `install.sh` | Creates .env, generates SSH keypair, builds containers |
| `docker-compose.yml` | All 4 services, 2 networks, 2 volumes |
| `.env.template` | All config vars with defaults |
| `core/mcp_server.py` | FastMCP server, single `run_command()` tool |
| `core/entrypoint.sh` | SSH key setup, sudo whitelist config, drops to `hcli` user via `gosu` |
| `claude-code/dispatcher.py` | BLPOP loop, session resume, Claude invocation, memory storage |
| `claude-code/mcp-config.json` | Points to `http://h-cli-core:8083/sse` |
| `telegram-bot/bot.py` | Async bot with `/run`, `/new`, `/status`, `/help` + natural language handler |
| `shared/hcli_logging/` | Shared stdlib-only JSON logging library (app.log, error.log, audit.log) |
| `log4ai/` | Standalone shell logger (bash/zsh), captures commands + output as JSONL |

## Data Flow

1. User sends message in Telegram
2. `telegram-bot` checks `ALLOWED_CHATS` (fail-closed), queues JSON task to `hcli:tasks`
3. `claude-code` dispatcher BLPOPs task, looks up session for this chat_id
4. Runs `claude -p <message> --resume <session>` (or `--session-id` for new)
5. Claude Code calls `run_command()` on core's MCP server via SSE
6. Core executes command as `hcli` user (sudo whitelist for nmap/tcpdump/etc.)
7. Result stored in `hcli:results:<task_id>` (TTL 600s)
8. Raw conversation stored in `hcli:memory:<task_id>:user` / `:asst`
9. `telegram-bot` polls result key, sends back to Telegram (splits at 4096 chars)

## Session Management

- Session IDs stored in `hcli:session:<chat_id>` with configurable TTL (default 4h)
- Dispatcher uses `--resume <session_id>` for existing sessions
- If resume fails, retries with a fresh session automatically
- `/new` command deletes the session key, forces fresh start

## Security Posture — 14/14 Items Implemented

| # | Item | How |
|---|------|-----|
| 1 | MCP port not exposed on host | No `ports:` mapping, internal network only |
| 2 | Redis authentication | `requirepass` via env var, written to `/tmp/redis.conf` at runtime |
| 3 | Network isolation | `h-network-frontend` (telegram+Redis) / `h-network-backend` (core), claude bridges both |
| 4 | Non-root user + sudo whitelist | `hcli` user, `SUDO_COMMANDS` resolved to full paths, fail-closed |
| 5 | cap_drop ALL on telegram-bot + claude-code | All 14 default capabilities dropped |
| 6 | no-new-privileges on telegram-bot + claude-code | Prevents setuid escalation |
| 7 | Read-only rootfs on telegram-bot + claude-code | `read_only: true`, tmpfs for `/tmp`, `/run` |
| 8 | Health checks on all services | MCP curl, Redis ping, Redis connectivity |
| 9 | Graceful shutdown (SIGTERM) | Dispatcher finishes current task, BLPOP timeout=30s |
| 10 | Input validation | Malformed JSON skipped, invalid ALLOWED_CHATS logged and ignored |
| 11 | Redis password not in process list | Written to file at runtime, not in `ps` |
| 12 | Redis memory cap + persistence | 2GB, allkeys-lru, RDB snapshots + AOF |
| 13 | Pinned Python dependencies | Major version ranges in all requirements.txt |
| 14 | Dedicated SSH keys | `install.sh` auto-generates ed25519 keypair, skips if keys exist |

**Intentionally skipped**: read-only rootfs on core (needs writable /tmp), cap_drop ALL on core (needs NET_RAW/NET_ADMIN), custom seccomp, TLS on Redis (isolated network), container resource limits (low traffic), tmpfs noexec on core (breaks tools). See `SECURITY-HARDENING.md`.

## Environment Variables (.env)

| Variable | Required | Default |
|----------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Yes | — |
| `ALLOWED_CHATS` | Yes | — (empty = nobody) |
| `REDIS_PASSWORD` | Yes | — |
| `SSH_KEYS_DIR` | No | `./ssh-keys` |
| `LOG_LEVEL` | No | `INFO` |
| `MAX_CONCURRENT_TASKS` | No | `3` |
| `TASK_TIMEOUT` | No | `300` |
| `SESSION_TTL` | No | `14400` (4h) |
| `SUDO_COMMANDS` | No | `nmap,tcpdump,traceroute,mtr,ping,ss,ip,iptables` |
| `NETBOX_URL` / `NETBOX_API_TOKEN` | No | — |
| `GRAFANA_URL` / `GRAFANA_API_TOKEN` | No | — |
| `OLLAMA_URL` / `OLLAMA_MODEL` | No | — |
| `VLLM_URL` / `VLLM_API_KEY` / `VLLM_MODEL` | No | — |

## Git History (35 commits, single branch `main`)

The project evolved in clear phases:

1. **Scaffolding** (`8c6f4cf`–`ba4e4c9`): Initial structure, README, ASCII diagram, JSON logging
2. **Core features** (`2027322`–`de4966f`): Telegram bot, Redis queue, Claude Code + MCP integration, session continuity
3. **Security hardening** (`390fd82`–`63a31d5`): Network isolation, Redis auth, non-root user, capability dropping, read-only rootfs
4. **Audit fixes** (`68a1edd`–`cc1e04a`): Service API env vars, sudo whitelist, priority fixes, dependency pinning, SSH keypair generation

No branches, no PRs — linear history on main. Remote: `git.hb-l.nl:halil/h-cli.git`.

## What's NOT Done Yet

| Item | Notes |
|------|-------|
| Circuit breaker for Claude timeouts | Deferred (priofixes.md #9), add if needed during testing |
| Selectable base image for core | Phase 2 (SECURITY-HARDENING.md #15), ParrotOS/Alpine/custom |
| Local LLM support | Env vars for Ollama/vLLM exist but not wired into dispatcher yet |
| RBAC / multi-user | Currently single-user, ALLOWED_CHATS is flat allowlist |
| Claude login automation | Still requires manual `docker compose run claude-code claude login` |
| Integration testing | No test suite yet, manual verification only |
| CI/CD | No pipeline, manual `git push` to Gitea |

## How to Deploy

```bash
git clone git.hb-l.nl:halil/h-cli.git && cd h-cli
./install.sh                                        # .env + SSH keypair + docker build
nano .env                                           # set tokens
ssh-copy-id -i ssh-keys/id_ed25519.pub user@host   # add key to managed servers
docker compose run claude-code claude login          # one-time auth
docker compose up -d                                # go
```

## Documentation Index

| File | Contents |
|------|----------|
| `README.md` | Full project docs, architecture, usage, config |
| `EXECUTIVE-SUMMARY.md` | One-page pitch |
| `SECURITY-HARDENING.md` | Security audit tracker (14/14 + phase 2 + skipped items) |
| `priofixes.md` | Priority bug/fix tracker (10/11 done, 1 deferred) |
| `ssh-keys/README.md` | SSH key setup (auto-generated + user-provided) |
| `log4ai/` | Standalone shell logger with its own `install.sh` |
