# Security

44 security items implemented. Full audit trail: [SECURITY-HARDENING.md](../SECURITY-HARDENING.md)

## Highlights

- **Asimov firewall**: MCP proxy between Claude and core. Two layers: deterministic pattern denylist (always active, zero latency) + independent Haiku gate check (on by default, resistant to conversational prompt injection)
- **Network isolation**: `h-network-frontend` (telegram-bot, Redis) and `h-network-backend` (core) are separate Docker networks — only claude-code bridges both
- **Fail-closed auth**: `ALLOWED_CHATS` allowlist — empty = nobody gets in
- **Non-root**: All containers run as `hcli` (uid 1000), not root
- **Capabilities**: `NET_RAW`/`NET_ADMIN` on core only; `cap_drop: ALL` + `no-new-privileges` on telegram-bot and claude-code; `read_only` rootfs on telegram-bot
- **Sudo whitelist**: only commands in `SUDO_COMMANDS` are allowed via sudo (resolved to full paths, fail-closed)
- **HMAC-signed results**: Dispatcher signs, telegram-bot verifies. Prevents Redis result spoofing.
- **Redis auth**: password-protected, 2GB memory cap, LRU eviction, RDB + AOF persistence
- **Session chunking**: Auto-rotate at 100KB, up to 50KB of recent context injected into system prompt
- **Tool restriction**: Claude Code restricted to `mcp__h-cli-core__run_command` only
- **Pinned deps**: all Python packages pinned to major version ranges, base images pinned

## Container Privileges

| Container | User | Capabilities | Rootfs | Networks |
|-----------|------|-------------|--------|----------|
| `telegram-bot` | `hcli` (1000) | None (`cap_drop: ALL`) | Read-only | frontend only |
| `redis` | `redis` (default) | Default | Writable | frontend only |
| `claude-code` | `hcli` (1000) | None (`cap_drop: ALL`) | Writable | frontend + backend |
| `core` | `hcli` (1000) | `NET_RAW`, `NET_ADMIN` | Writable | backend only |

## Data Access

| Container | Redis | Filesystem writes | Secrets it holds |
|-----------|-------|-------------------|------------------|
| `telegram-bot` | Read/write (task queue + results) | Logs only | `TELEGRAM_BOT_TOKEN`, `REDIS_PASSWORD`, `RESULT_HMAC_KEY` |
| `redis` | N/A (is the store) | `/data` (RDB + AOF) | `REDIS_PASSWORD` |
| `claude-code` | Read/write (tasks, sessions, memory) | Logs, session chunks, `~/.claude/` | `REDIS_PASSWORD`, `RESULT_HMAC_KEY`, Claude credentials (volume) |
| `core` | None | Logs only | SSH keys (copied at startup), integration tokens (NetBox, Grafana, EVE-NG) |

## Sudo Whitelist (core only)

Commands in `SUDO_COMMANDS` are resolved to full paths at startup. Default:

```
nmap, tcpdump, traceroute, mtr, ping, ss, ip, iptables
```

Everything else is denied. Fail-closed — if a command isn't in the list, sudo refuses it.

## Optional Integrations

| Integration | Container | Access | Required scope |
|-------------|-----------|--------|----------------|
| NetBox | `core` | REST API (read) | Read-only API token recommended |
| Grafana | `core` | REST API (read) | Viewer role token recommended |
| EVE-NG | `core` | REST API (read/write) | Lab user credentials |

All integration tokens live only in core's environment. No other container sees them.
