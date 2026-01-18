# Priority Fixes

## Critical

### 1. ~~No health checks on any service~~ FIXED
Added healthcheck stanzas to all four services in docker-compose.yml.

### 2. ~~No SIGTERM handling in dispatcher~~ FIXED
Added `signal.SIGTERM` handler with graceful shutdown flag. Dispatcher finishes current task before exiting.

### 3. ~~BLPOP with infinite timeout~~ FIXED
Changed `timeout=0` to `timeout=30` so the loop checks `_shutdown` flag every 30s.

## High

### 4. ~~json.loads() without error handling~~ FIXED
Added try/except JSONDecodeError in both dispatcher.py and bot.py. Malformed payloads are logged and skipped.

### 5. ~~ALLOWED_CHATS crashes on bad input~~ FIXED
Replaced set comprehension with per-ID try/except. Invalid IDs are logged and skipped instead of crashing.

### 6. ~~Unquoted variable in entrypoint~~ FIXED
Quoted `$SSH_STAGING` in the `ls -A` check.

### 7. ~~Redis password visible in process list~~ FIXED
Redis now generates its config at runtime via `sh -c 'printf ... > /tmp/redis.conf'`. Password is in env var only, not in `ps aux`.

## Medium

### 8. ~~No resource limits / Redis memory cap~~ FIXED
Redis capped at 2GB with `allkeys-lru` eviction. RDB snapshots + AOF enabled for persistence.

### 9. Circuit breaker for Claude timeouts — DEFERRED
Will add if needed during testing.

### 10. Pin Python dependencies — TODO
Pin versions in requirements.txt before release.

### 11. User messages logged verbatim — ACCEPTED
Audit logs are local-only, single-user product. Acceptable risk.
