# Security Hardening Status

Based on container security audit (Feb 2026). Tracks what's implemented, what's open, and what's skipped.

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

### 15. Asimov firewall — pattern denylist
Deterministic string matching against `BLOCKED_PATTERNS` env var (pipe-separated) and/or `BLOCKED_PATTERNS_FILE` (one per line, for external CVE/signature feeds). Catches obfuscation tricks like `| bash`, `base64 -d`, etc. Always active regardless of `GATE_CHECK` setting. Zero latency, no LLM call. Runs inside `firewall.py` MCP proxy before any command reaches core.

### 16. Asimov firewall — Haiku gate check
Independent Haiku one-shot that sees ONLY `groundRules.md` + the command. No conversation history, no user context — immune to prompt injection. Returns ALLOW/DENY with reason. Enabled via `GATE_CHECK=true`. Adds ~2-3s latency per command. Ambiguous or failed responses fail closed (DENY). Both layers log to `/var/log/hcli/firewall/` with full audit trail.

### 17. Gate subprocess cleanup on timeout/error
Haiku gate subprocess is explicitly killed (`proc.kill()` + `await proc.wait()`) on timeout or exception. Prevents file descriptor leaks and zombie processes over prolonged operation.

### 18. Session chunk write error handling
`dump_session_chunk()` wraps file I/O in try/except. Redis state (history + size keys) is only cleared after a successful file write. Disk full or permission errors return None instead of crashing `process_task()`, preventing orphaned Redis keys.

### 19. Redis socket timeouts on telegram-bot
Connection pool created with `socket_connect_timeout=5` and `socket_timeout=10`. Prevents handlers from blocking forever if Redis hangs (not crashed, just unresponsive).

---

## Open Findings (from code audit, Feb 12 2026)

### CRITICAL

*None — all critical findings resolved.*

### HIGH

#### ~~F2. Session chunk write has no error handling~~ FIXED (item 18)

#### ~~F3. No socket timeout on Redis connection pool (telegram-bot)~~ FIXED (item 19)

### MEDIUM

#### F4. Ground rules file missing = silent gate bypass
**File:** `claude-code/firewall.py` — startup
If `groundRules.md` is missing and `GATE_CHECK=true`, the gate check always returns ALLOW because the prompt has no rules to check against. Should fail hard at startup if gate is enabled but rules file missing.

#### F5. Patterns file missing = silent failure
**File:** `claude-code/firewall.py` — startup
If `BLOCKED_PATTERNS_FILE` is set but the file doesn't exist, a warning is logged but the firewall starts with zero file-based patterns. Should fail hard if explicitly configured.

#### F6. Dispatcher healthcheck doesn't check dispatcher
**File:** `docker-compose.yml` — claude-code healthcheck
Current check (`python3 -c "import redis; redis.from_url(...).ping()"`) only verifies Redis is up. If dispatcher is stuck in a subprocess or hung BLPOP, container is still marked healthy. Need heartbeat file approach.

#### F7. Timeouts not synchronized across stack
Telegram bot waits 300s, dispatcher subprocess times out at 290s, core command times out at 280s, gate check times out at 30s. These don't cascade cleanly — a gate timeout doesn't fail-fast to the user.

#### F8. `parrotsec/core:latest` not pinned
**File:** `core/Dockerfile`
Using `:latest` tag means builds are not reproducible. Should pin to specific version.

---

## Phase 2

### Selectable base image for core
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
- **Commands logged in plain text** — local-only logs, single-user product, accepted risk
- **Session rotation not atomic** — single dispatcher, no concurrency, accepted risk
- **Bot blocks on result polling** — by design, user sees typing indicator
