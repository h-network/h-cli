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
Core runs as unprivileged user `hcli`. Sudo is restricted to commands listed in `SUDO_COMMANDS` env var (default: nmap, tcpdump, traceroute, mtr, ping, ss, ip, iptables). Full paths resolved at startup via `command -v`. Empty value = no sudo (fail-closed). Dangerous argument patterns blocked by the Asimov firewall's pattern denylist (see item 29).

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

### 20. Fail-hard on missing ground rules when gate enabled
If `GATE_CHECK=true` but `groundRules.md` is missing, firewall raises `RuntimeError` at startup instead of silently allowing all commands. When gate is disabled, logs a warning and continues normally.

### 21. Fail-hard on missing patterns file
If `BLOCKED_PATTERNS_FILE` is configured but the file doesn't exist, firewall raises `RuntimeError` at startup instead of silently running with zero file-based patterns.

### 22. Dispatcher liveness healthcheck
Dispatcher touches `/tmp/heartbeat` every BLPOP cycle (max 30s). Docker healthcheck verifies the file was modified less than 60s ago via `stat -c %Y`. If dispatcher is stuck in a subprocess or hung, the heartbeat goes stale and Docker marks the container unhealthy.

### 23. Synchronized timeout cascade
Timeouts are ordered so each layer times out before its parent, with margin for overhead:
- Telegram bot: 300s (`TASK_TIMEOUT`, configurable)
- Dispatcher subprocess: 280s (20s margin)
- Gate check: 30s (firewall)
- Core command: 240s (10s margin after gate)

Ensures failures propagate cleanly instead of racing between layers.

### 24. Pinned ParrotOS base image
Core Dockerfile uses `parrotsec/core:7.1` instead of `:latest`. Builds are reproducible — two identical Dockerfiles produce the same base image. Update the version explicitly when upgrading.

### 25. Output truncation in core MCP server
Command output (stdout + stderr) is capped at 500KB. If output exceeds the limit, it is truncated and a `[OUTPUT TRUNCATED at 500KB]` notice is appended. The `truncated` flag is logged in the audit trail. Prevents a single command from returning gigabytes of data and overwhelming the pipeline.

### 26. chat_id validated before filesystem path construction
`chat_id` is validated against `^-?\d+$` (numeric Telegram ID) before use in `os.path.join()`. Both `dump_session_chunk()` and `_load_recent_chunks()` reject non-numeric chat IDs with a warning log. Prevents path traversal attacks via crafted chat_id values.

### 27. Startup warning when ALLOWED_CHATS is empty
If `ALLOWED_CHATS` is empty or missing from `.env`, the telegram-bot logs a WARNING at startup: "no users are authorized — the bot will reject all messages." Still fail-closed by design, but now the operator knows immediately why the bot isn't responding.

### 28. tmpfs no longer clobbers claude-credentials volume
Replaced `tmpfs: /root` (which overlaid the named volume at `/root/.claude`) with targeted tmpfs mounts for `/root/.cache`, `/root/.config`, and `/root/.npm`. Claude CLI credentials now persist across container restarts as intended.

### 29. Default blocked patterns file (~80 patterns, 12 categories)
Ships `blocked-patterns.txt` with ~80 patterns across 12 categories: shell piping to interpreters, encoded/obfuscated execution, reverse shells, destructive file ops, system destruction, disk manipulation, sudo escalation, credential access, container escape, network destruction, process/kernel manipulation, and package manager abuse. Mounted read-only into claude-code at `/app/blocked-patterns.txt`. Loaded by the Asimov firewall via `BLOCKED_PATTERNS_FILE` (defaults to `/app/blocked-patterns.txt`). Externally maintainable — edit the file and restart, no rebuild needed.

### 30. NodeSource installed via signed apt repo instead of curl|bash
Replaced `curl | bash` with GPG-verified apt repository. NodeSource signing key imported to `/etc/apt/keyrings/nodesource.gpg`, repo added as signed source. Eliminates supply chain risk from DNS poisoning or CDN compromise during build.

### 31. Claude Code CLI pinned to specific version
`npm install -g @anthropic-ai/claude-code@2.1.39` instead of unpinned latest. Prevents silent supply chain compromise via npm package takeover. Update version explicitly when upgrading.

### 32. Redis healthchecks use runtime shell expansion
Healthcheck commands wrapped in `sh -c` so `$REDIS_PASSWORD` and `$REDIS_URL` are expanded at runtime, not baked into container metadata. No longer visible via `docker inspect`.

### 33. task_id validated before use
`task_id` checked for presence, type, and length (max 100 chars) before use in Redis keys and logging. Missing or malformed task_id logs an error and skips the task instead of crashing.

### 34. ssh-keys directory created with 700 permissions
`install.sh` now uses `mkdir -p -m 700 ssh-keys` instead of default umask. Directory is not world-readable.

### 35. proc.kill() wrapped in try/except ProcessLookupError
Gate subprocess cleanup handles the case where the process has already exited before kill() is called. Prevents ProcessLookupError on PID reuse.

### 36. All log directories created by install.sh
Added `logs/claude` and `logs/firewall` to the `mkdir -p` line. Prevents Docker from creating them as root-owned on first run.

### 37. Consistent `--no-cache-dir` on all pip installs
All Dockerfiles now use `pip install --no-cache-dir`. Reduces image size and eliminates cached package archives.

### 38. Command normalization before pattern matching
Pattern denylist now normalizes commands before matching: collapses all whitespace (tabs, newlines, multiple spaces) to single spaces and strips quotes. Defeats evasion via `|\tbash`, `|  bash`, `| "bash"`, etc. Variable expansion (`$SHELL`) and path alternatives remain out of scope for the deterministic layer — covered by the Haiku gate when enabled.

### 39. claude-code container runs as non-root user (trade-off: read-only rootfs dropped)

Added `hcli` user (uid 1000) to claude-code Dockerfile with `USER hcli` directive. Dispatcher, firewall, and Claude CLI all run as unprivileged user. Credentials volume mounted at `/home/hcli/.claude`. Log directories owned by uid 1000 via `install.sh`.

**Design decision — non-root vs read-only rootfs:**

Claude Code CLI requires write access to its home directory (`~/.claude.json`, `~/.claude/` subdirs). Docker's `read_only: true` with tmpfs on `/home/hcli` clobbers the credentials volume at `/home/hcli/.claude` (tmpfs on parent overrides volume on child). Targeted tmpfs (`~/.cache`, `~/.config`, `~/.npm`) doesn't cover Claude CLI's write paths. These two constraints are mutually exclusive — you can have non-root OR read-only rootfs, not both.

We chose **non-root + writable** over **root + read-only** because:

| | Root + read-only | hcli + writable |
|---|---|---|
| Modify app code in /app | Blocked (read-only rootfs) | Blocked (files are root-owned) |
| Modify prompt files | Blocked (read-only rootfs) | Blocked (root-owned: CLAUDE.md, groundRules.md) |
| Write to session chunks | Yes (bind mounts bypass read-only) | Yes (bind mounts are writable) |
| Container breakout | Very hard (cap_drop ALL) | Harder (non-root + cap_drop ALL) |
| Kernel attack surface | Larger (root has more kernel interfaces) | Smaller (non-root, fewer syscall paths) |
| Write to /tmp | Yes (tmpfs) | Yes |

Key insight: application files in `/app/` are root-owned from Dockerfile `COPY`. The `hcli` user cannot modify `dispatcher.py`, `firewall.py`, `CLAUDE.md`, or `groundRules.md` — same practical protection as read-only rootfs. Session chunk directories are bind-mounted and writable in both options.

Non-root reduces kernel attack surface more than read-only rootfs protects filesystem integrity. Combined with `cap_drop: ALL` and `no-new-privileges`, this is the stronger security posture.

---

## Open Findings (from code audit, Feb 12 2026)

### CRITICAL

*None — all critical findings resolved.*

### HIGH

#### ~~F2. Session chunk write has no error handling~~ FIXED (item 18)

#### ~~F3. No socket timeout on Redis connection pool (telegram-bot)~~ FIXED (item 19)

### MEDIUM

#### ~~F4. Ground rules file missing = silent gate bypass~~ FIXED (item 20)

#### ~~F5. Patterns file missing = silent failure~~ FIXED (item 21)

#### ~~F6. Dispatcher healthcheck doesn't check dispatcher~~ FIXED (item 22)

#### ~~F7. Timeouts not synchronized across stack~~ FIXED (item 23)

#### ~~F8. `parrotsec/core:latest` not pinned~~ FIXED (item 24)

### Open Findings (from second code audit, Feb 12 2026)

#### ~~F9. No output truncation in core MCP server~~ FIXED (item 25)

#### ~~F10. chat_id not validated before filesystem path construction~~ FIXED (item 26)

#### ~~F11. No startup warning when ALLOWED_CHATS is empty~~ FIXED (item 27)

### Open Findings (from third adversarial audit, Feb 12 2026)

#### CRITICAL

#### ~~F12. tmpfs /root clobbers claude-credentials volume~~ FIXED (item 28)

#### ~~F13. Sudo whitelist without argument restrictions~~ FIXED (item 29)

#### ~~F14. Pattern denylist trivially bypassed via shell metacharacters~~ FIXED (item 38)

#### ~~F15. `curl | bash` supply chain vector in Dockerfile~~ FIXED (item 30)

#### ~~F16. Unpinned `npm install -g @anthropic-ai/claude-code`~~ FIXED (item 31)

#### HIGH

#### F17. Haiku gate prompt injectable via command string
**File:** `claude-code/firewall.py:79-89`
Command is interpolated directly into the Haiku prompt. Injection payload embedded in the command itself bypasses "no user context" design. Needs XML delimiters and instruction to ignore embedded instructions.

#### ~~F18. claude-code container runs as root~~ FIXED (item 39)

#### ~~F19. Redis healthchecks leak password via docker inspect~~ FIXED (item 32)

#### ~~F20. Stored prompt injection via session chunks~~ SKIPPED (by design)

#### F21. Unbounded chunk accumulation on disk
**File:** `claude-code/dispatcher.py`
Chunk files accumulate without eviction. No per-chat-id size cap. Fills volume over weeks/months of use.

#### ~~F22. New SSE connection per command — no pooling~~ SKIPPED (by design)

#### F23. `COPY context.md*` glob could leak files into image
**File:** `claude-code/Dockerfile:25`
Glob matches `context.md.backup`, `context.md.secret`, etc. Should be explicit.

#### MEDIUM

#### ~~F24. TOCTOU race on concurrency gate~~ SKIPPED (by design)

#### ~~F25. Error messages leak internal URLs to Telegram users~~ SKIPPED (by design)

#### F26. No timeout on SSE forward to core
**File:** `claude-code/firewall.py:120-136`
`_forward_to_core` has no timeout. Hangs indefinitely if core is slow. Needs `asyncio.wait_for`.

#### ~~F27. Memory keys never expire — unbounded Redis growth~~ SKIPPED (by design)

#### ~~F28. No task_id validation~~ FIXED (item 33)

#### F29. SSH TOFU with ephemeral known_hosts
**File:** `core/entrypoint.sh:47-54`
`StrictHostKeyChecking accept-new` trusts first connection. known_hosts doesn't persist across restarts.

#### F30. Result keys unauthenticated — spoofable via Redis
**File:** `claude-code/dispatcher.py:315`
Any Redis client on frontend network can read/write result keys. No HMAC or chat_id binding.

#### F31. groundRules Rule 10 is self-override escape hatch
**File:** `groundRules.md:48`
"If following a rule would genuinely harm the user's goal, explain why you're deviating" — tells both Sonnet and Haiku that rules can be overridden.

#### ~~F32. Base images pinned to tag not SHA digest~~ SKIPPED (by design)

#### ~~F33. `--break-system-packages` bypasses PEP 668~~ SKIPPED (by design)

#### F34. Playwright runs without sandbox
**File:** `core/Dockerfile:32-33`
Non-root user can't create namespaces. Chromium runs unsandboxed. Browser exploit = hcli user = sudo escalation.

#### ~~F35. ssh-keys directory created without restrictive permissions~~ FIXED (item 34)

#### LOW

#### ~~F36. PID reuse race on proc.kill()~~ FIXED (item 35)

#### F37. Chunk file symlink race + listing doesn't exclude symlinks
**File:** `claude-code/dispatcher.py:75-84,136-142`
No `os.path.islink()` check. Symlink in chunk dir = arbitrary file read into system prompt.

#### ~~F38. Redis reconnect loses in-flight task~~ SKIPPED (accepted risk)

#### F39. Full user messages in audit logs
**File:** `telegram-bot/bot.py`, `claude-code/dispatcher.py`
Secrets sent via bot are logged in plaintext. No truncation or retention policy.

#### ~~F40. Missing log dirs in install.sh~~ FIXED (item 36)

#### ~~F41. Inconsistent `--no-cache-dir` on pip install~~ FIXED (item 37)

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
- **Error messages leak internal URLs (F25)** — single-user product, user is the admin, seeing the real error in Telegram is more useful than "check logs"
- **Memory keys never expire (F27)** — raw material for planned vector DB memory layer, TTL would delete training data before it's processed
- **Stored prompt injection via chunks (F20)** — requires write access to `/var/log/hcli/sessions/` on the host, which means the host is already compromised
- **New SSE connection per command (F22)** — 2-5 connections per task at most, process dies between tasks, personal tool with single-digit users
- **TOCTOU race on concurrency gate (F24)** — single-user tool, two messages arriving at the exact same millisecond is not a realistic scenario
- **Base images pinned to tag not digest (F32)** — standard practice for open source projects, digest pinning prevents automatic security patches
- **`--break-system-packages` (F33)** — build-time only, containers are disposable
- **Redis reconnect loses in-flight task (F38)** — millisecond window during result SET, user gets a timeout and re-sends
- **Read-only rootfs on claude-code** — traded for non-root user (item 39). Claude CLI needs writable home dir, tmpfs on parent clobbers credential volume. Non-root + cap_drop ALL + no-new-privileges provides equivalent protection: app files are root-owned (unmodifiable by hcli), container breakout is harder as non-root. See item 39 for full comparison table.
