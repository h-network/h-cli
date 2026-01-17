# Security Hardening Status

Based on container security audit (Feb 2026). Tracks what's implemented and what's left.

## Implemented

### 1. MCP port not exposed on host
The `ports:` mapping for 8083 was never added to docker-compose.yml. Claude-code reaches core via `h-bot-core:8083` on the internal `h-backend` network only.

### 2. Redis authentication
Redis runs with `requirepass`. All services connect via authenticated `redis://` URLs. Password set via `REDIS_PASSWORD` env var.

### 3. Network isolation
Two separate Docker networks:
- **h-frontend**: telegram-bot + Redis
- **h-backend**: core

Claude-code bridges both. Telegram-bot cannot reach core directly.

### 4. Non-root user + scoped sudo whitelist
Core runs as unprivileged user `hbot`. Sudo is restricted to commands listed in `SUDO_COMMANDS` env var (default: nmap, tcpdump, traceroute, mtr, ping, ss, ip, iptables). Full paths resolved at startup via `command -v`. Empty value = no sudo (fail-closed).

### 5. cap_drop: ALL on telegram-bot and claude-code
Both containers drop all 14 default Linux capabilities.

### 6. no-new-privileges on telegram-bot and claude-code
Prevents privilege escalation via setuid binaries.

### 7. Read-only rootfs on telegram-bot and claude-code
Both containers run with `read_only: true`. Writable paths limited to `tmpfs` mounts (`/tmp`, `/run`) and bind-mounted log directories.

## Nice to Have

### 8. Dedicated SSH keys
Generate bot-specific SSH keys on first run instead of sharing the user's personal keys. Separate identity, easy to revoke, cleaner audit trail.

## Phase 2

### 9. Selectable base image for core
Let the user choose their toolbox — ParrotOS for pentesting, Alpine for lightweight ops, custom for specific workloads. Modular core images.

---

## Skipped (intentional)

These were flagged in the audit but do not apply to this project:

- **Read-only rootfs on core** — the container is designed for shell access, needs writable /tmp
- **cap_drop ALL on core** — needs NET_RAW + NET_ADMIN for nmap/tcpdump
- **Custom seccomp profiles** — default profile is sufficient, custom adds complexity with minimal gain
- **shell=True in mcp_server.py** — shell access is the product, can't avoid it
- **TLS on Redis** — isolated Docker network is sufficient
- **Resource limits** — containers are idle most of the time, low traffic
- **tmpfs noexec on core** — would break tool execution
