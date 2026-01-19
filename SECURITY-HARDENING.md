# Security Hardening Status

Based on container security audit (Feb 2026). Tracks what's implemented and what's left.

## Implemented

### 1. MCP port not exposed on host
The `ports:` mapping for 8083 was never added to docker-compose.yml. Claude-code reaches core via `h-cli-core:8083` on the internal `h-network-backend` network only.

### 2. Redis authentication
Redis runs with `requirepass`. All services connect via authenticated `redis://` URLs. Password set via `REDIS_PASSWORD` env var.

### 3. Network isolation
Two separate Docker networks:
- **h-network-frontend**: telegram-bot + Redis
- **h-network-backend**: core

Claude-code bridges both. Telegram-bot cannot reach core directly.

### 4. Non-root user + scoped sudo whitelist
Core runs as unprivileged user `hcli`. Sudo is restricted to commands listed in `SUDO_COMMANDS` env var (default: nmap, tcpdump, traceroute, mtr, ping, ss, ip, iptables). Full paths resolved at startup via `command -v`. Empty value = no sudo (fail-closed).

### 5. cap_drop: ALL on telegram-bot and claude-code
Both containers drop all 14 default Linux capabilities.

### 6. no-new-privileges on telegram-bot and claude-code
Prevents privilege escalation via setuid binaries.

### 7. Read-only rootfs on telegram-bot and claude-code
Both containers run with `read_only: true`. Writable paths limited to `tmpfs` mounts (`/tmp`, `/run`) and bind-mounted log directories.

### 8. Health checks on all services
All four containers have Docker healthcheck stanzas: core checks MCP endpoint via curl, Redis checks via `redis-cli ping`, telegram-bot and claude-code verify Redis connectivity via Python.

### 9. Graceful shutdown (SIGTERM)
Dispatcher registers a SIGTERM handler. On `docker stop`, it finishes the current task before exiting. BLPOP uses `timeout=30` so the shutdown flag is checked every 30 seconds.

### 10. Input validation
- Malformed JSON payloads from Redis are caught, logged, and skipped (no crash)
- Invalid entries in `ALLOWED_CHATS` are logged and ignored instead of crashing startup

### 11. Redis password not in process list
Redis password is passed via environment variable and written to `/tmp/redis.conf` at container startup. Not visible in `ps aux` output.

### 12. Redis memory cap + persistence
Redis capped at 2GB with `allkeys-lru` eviction policy. RDB snapshots (every 5min/10 changes, 15min/1 change) and AOF enabled for crash recovery. No data loss on reboot.

### 13. Pinned Python dependencies
All Python packages pinned to major version ranges (`>=X.Y,<next_major`) in requirements.txt files and Dockerfiles. Prevents breaking changes on rebuild while allowing patch updates.

### 14. Dedicated SSH keys
`install.sh` auto-generates an ed25519 keypair into `ssh-keys/` on first run. Separate identity, easy to revoke, cleaner audit trail. Skipped if user already has keys in place.

## Phase 2

### 15. Selectable base image for core
Let the user choose their toolbox — ParrotOS for pentesting, Alpine for lightweight ops, custom for specific workloads. Modular core images.

---

## Skipped (intentional)

These were flagged in the audit but do not apply to this project:

- **Read-only rootfs on core** — the container is designed for shell access, needs writable /tmp
- **cap_drop ALL on core** — needs NET_RAW + NET_ADMIN for nmap/tcpdump
- **Custom seccomp profiles** — default profile is sufficient, custom adds complexity with minimal gain
- **shell=True in mcp_server.py** — shell access is the product, can't avoid it
- **TLS on Redis** — isolated Docker network is sufficient
- **Container resource limits** — containers are idle most of the time, low traffic (Redis is capped at 2GB)
- **tmpfs noexec on core** — would break tool execution
