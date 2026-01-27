# h-cli Project Status

> Updated: 2026-02-12 | Git: `5e5af5a` on `main` | 65 commits

## What This Is

Natural language infrastructure management via Telegram. User sends a message, Claude Code interprets it, executes commands in a hardened ParrotOS container, returns results. Session context persists for 4 hours with automatic chunking at 100KB.

Part of the **h-ecosystem** — a self-improving AI ops pipeline where h-cli collects structured interaction data (JSONL via log4ai) that feeds into training pipelines downstream.

## Architecture

Four containers, two isolated Docker networks:

```
Telegram --> telegram-bot --> Redis --> claude-code --> firewall.py --> core (ParrotOS)
             h-network-frontend          bridges both                   h-network-backend
```

| Container | Image | Role |
|-----------|-------|------|
| `h-cli-telegram` | `python:3.12-slim` | Telegram polling bot, auth gatekeeper, queues tasks to Redis |
| `h-cli-redis` | `redis:7-alpine` | Message queue (`hcli:tasks`), result store, session storage |
| `h-cli-claude` | `ubuntu:24.04` + Node 22 + Claude Code CLI | BLPOP dispatcher, invokes `claude -p` with MCP + session resume. Includes Asimov firewall (MCP proxy) |
| `h-cli-core` | `parrotsec/core:7.1` | FastMCP SSE server exposing `run_command()`, nmap/dig/mtr/ssh/Playwright |

Claude Code uses the user's Max/Pro subscription (zero API cost). Auth via `docker compose run claude-code claude login`, credentials persist in a Docker volume.

## Key Files

| File | Purpose |
|------|---------|
| `install.sh` | Creates .env, generates SSH keypair, builds containers |
| `docker-compose.yml` | All 4 services, 2 networks, 2 volumes |
| `.env.template` | All config vars with defaults |
| `core/mcp_server.py` | FastMCP server, single `run_command()` tool |
| `core/entrypoint.sh` | SSH key setup, sudo whitelist config, drops to `hcli` user via `gosu` |
| `claude-code/dispatcher.py` | BLPOP loop, session resume, session chunking, Claude invocation, memory storage |
| `claude-code/firewall.py` | Asimov firewall — MCP proxy between Sonnet and core. Pattern denylist + optional Haiku gate check |
| `claude-code/mcp-config.json` | Points to firewall proxy (stdio transport), not directly to core |
| `telegram-bot/bot.py` | Async bot with `/run`, `/new`, `/status`, `/help` + natural language handler |
| `shared/hcli_logging/` | Shared stdlib-only JSON logging library (app.log, error.log, audit.log) |
| `log4ai/` | Standalone shell logger (bash/zsh), captures commands + output as JSONL |
| `groundRules.md` | Asimov-inspired safety directives, injected into system prompt |
| `context.md` | User's deployment description (gitignored), injected into system prompt |

## Data Flow

1. User sends message in Telegram
2. `telegram-bot` checks `ALLOWED_CHATS` (fail-closed), queues JSON task to `hcli:tasks`
3. `claude-code` dispatcher BLPOPs task, looks up session for this chat_id
4. Builds system prompt from `groundRules.md` + `context.md` + session chunk history (up to 50KB)
5. Runs `claude -p <message> --resume <session>` (or `--session-id` for new)
6. Claude Code calls `run_command()` — routed through `firewall.py` (MCP proxy)
7. Firewall runs pattern denylist check (always active), then optional Haiku gate check (`GATE_CHECK=true`)
8. If allowed, firewall forwards to core's MCP server via SSE
9. Core executes command as `hcli` user (sudo whitelist for nmap/tcpdump/etc.)
10. Result stored in `hcli:results:<task_id>` (TTL 600s)
11. Raw conversation stored in `hcli:memory:<task_id>:user` / `:asst`
12. `telegram-bot` polls result key, sends back to Telegram (splits at 4096 chars)

## Session Management

- Session IDs stored in `hcli:session:<chat_id>` with configurable TTL (default 4h)
- Dispatcher uses `--resume <session_id>` for existing sessions
- If resume fails, retries with a fresh session automatically
- `/new` command deletes the session key, forces fresh start
- **Session chunking**: When accumulated size exceeds 100KB, history is dumped to `/var/log/hcli/sessions/{chat_id}/chunk_{timestamp}.txt` and Redis state is cleared
- Chunks are mounted read-only at `/app/sessions` inside the claude-code container so Claude Code's sandbox can read prior context
- Up to 50KB of recent chunks are injected into the system prompt per task

## Asimov Firewall

The firewall (`claude-code/firewall.py`) is an MCP proxy that sits between Sonnet and the core MCP server. All `run_command()` calls pass through it.

**Two layers of defense:**

1. **Pattern denylist** (always active, zero latency) — deterministic string matching against `BLOCKED_PATTERNS` env var (pipe-separated) and/or `BLOCKED_PATTERNS_FILE` (one per line, for external CVE/signature feeds). Catches obfuscation tricks like `| bash`, `base64 -d`, etc.

2. **Haiku gate check** (optional, `GATE_CHECK=true`, ~2-3s latency) — independent Haiku one-shot that sees ONLY `groundRules.md` + the command. No user context (immune to prompt injection). Returns ALLOW/DENY with reason.

Both layers log to `/var/log/hcli/firewall/` with full audit trail.

## Security Posture — 26 Items Implemented

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
| 15 | Asimov firewall — pattern denylist | Deterministic string matching, always active, zero latency |
| 16 | Asimov firewall — Haiku gate check | Independent LLM gate, sees only groundRules + command, optional |
| 17 | Gate subprocess cleanup | `proc.kill()` + `await proc.wait()` on timeout/error, prevents FD leaks |
| 18 | Session chunk write error handling | File I/O wrapped in try/except, Redis cleared only on success |
| 19 | Redis socket timeouts (telegram-bot) | `socket_connect_timeout=5`, `socket_timeout=10`, prevents infinite hangs |
| 20 | Fail-hard on missing ground rules | `RuntimeError` at startup if `GATE_CHECK=true` but `groundRules.md` missing |
| 21 | Fail-hard on missing patterns file | `RuntimeError` at startup if `BLOCKED_PATTERNS_FILE` set but file missing |
| 22 | Dispatcher liveness healthcheck | Heartbeat file touched every BLPOP cycle, Docker checks staleness < 60s |
| 23 | Synchronized timeout cascade | Telegram 300s → dispatcher 280s → gate 30s / core 240s |
| 24 | Pinned ParrotOS base image | `parrotsec/core:7.1` instead of `:latest`, reproducible builds |
| 25 | Output truncation in core MCP server | stdout+stderr capped at 500KB, truncation notice appended, logged in audit |
| 26 | chat_id path validation | Validated as numeric before filesystem path construction, prevents traversal |

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
| `GATE_CHECK` | No | `false` |
| `BLOCKED_PATTERNS` | No | — (pipe-separated, e.g. `\| bash\|base64 -d`) |
| `BLOCKED_PATTERNS_FILE` | No | — (path to pattern file, one per line, for external CVE/signature feeds) |
| `NETBOX_URL` / `NETBOX_API_TOKEN` | No | — |
| `GRAFANA_URL` / `GRAFANA_API_TOKEN` | No | — |
| `OLLAMA_URL` / `OLLAMA_MODEL` | No | — |
| `VLLM_URL` / `VLLM_API_KEY` / `VLLM_MODEL` | No | — |

## Git History (65 commits, single branch `main`)

The project evolved in clear phases:

1. **Scaffolding** (`8c6f4cf`–`ba4e4c9`): Initial structure, README, ASCII diagram, JSON logging
2. **Core features** (`2027322`–`de4966f`): Telegram bot, Redis queue, Claude Code + MCP integration, session continuity
3. **Security hardening** (`390fd82`–`63a31d5`): Network isolation, Redis auth, non-root user, capability dropping, read-only rootfs
4. **Audit fixes** (`68a1edd`–`cc1e04a`): Service API env vars, sudo whitelist, priority fixes, dependency pinning, SSH keypair generation
5. **Session memory** (`2bbdf52`–`c1dc011`): Ground rules + context system prompt, session chunking at 100KB, chunk injection into system prompt
6. **Asimov firewall** (`e3d21f9`–`4324dcc`): MCP proxy with Haiku gate check, deterministic pattern denylist, session chunks mounted at /app/sessions
7. **Audit fixes round 2** (`496b85b`–`d3ec7b6`): Gate subprocess cleanup, session chunk error handling, Redis socket timeouts, fail-hard configs, dispatcher heartbeat, timeout cascade, pinned base image

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
| Sudo whitelist verbosity | Arguments not constrained, gatekeeper covers this for now |
| Session resume retry hardening | Single retry, should be up to 3 with user notification on context loss |
| ~~Dispatcher healthcheck~~ | ~~No heartbeat file~~ — FIXED (security item 22) |
| Vector DB memory layer | Planned, not yet implemented |

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
| `SECURITY-HARDENING.md` | Security audit tracker (26 items + 1 open finding + skipped items) |
| `priofixes.md` | Priority bug/fix tracker (10/11 done, 1 deferred) |
| `groundRules.md` | Safety directives injected into system prompt |
| `context.md.template` | Template for user's deployment description |
| `ssh-keys/README.md` | SSH key setup (auto-generated + user-provided) |
| `log4ai/` | Standalone shell logger with its own `install.sh` |
