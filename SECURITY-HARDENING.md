# Security Hardening — Immediate Next Steps

Based on container security audit (Feb 2026).

## Critical (do first)

### 1. Remove MCP port exposure
Remove `ports: 8083:8083` from h-bot-core in docker-compose.yml. Claude-code reaches core via `h-bot-core:8083` on h-network — the host port is unnecessary and exposes a remote shell to the entire LAN.

### 2. Redis authentication
Add `requirepass` to Redis config. Pass `REDIS_PASSWORD` as env var to telegram-bot, claude-code, and core. Without this, any container on h-network has full unauthenticated Redis access.

### 3. Network redesign
Separate h-network into isolated segments so containers can only reach what they need:

```
telegram-bot  ──►  redis  ◄──  claude-code  ──►  core
     │                              │
     └──── frontend-net ────────────┘
                                    │
                              backend-net ────── core
```

telegram-bot should not be able to reach core directly.

## Best Practice (one commit)

### 4. Non-root user + passwordless sudo on core
Add a dedicated user in the core Dockerfile. Add passwordless sudo for commands that need root. Container escape lands as unprivileged user on host instead of root.

### 5. cap_drop: ALL on telegram-bot and claude-code
These containers need zero Linux capabilities. Drop all 14 defaults.

### 6. no-new-privileges on telegram-bot and claude-code
Prevent privilege escalation via setuid binaries.

### 7. Read-only rootfs on telegram-bot and claude-code
These have predictable write patterns (logs are already bind-mounted). Lock down the filesystem.

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
