"""h-cli Claude Code dispatcher — BLPOP loop that invokes claude -p per task."""

import hashlib
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import redis

# Optional: TimescaleDB metrics
TIMESCALE_URL = os.environ.get("TIMESCALE_URL", "")
_pg_pool = None

def _get_pg_pool():
    """Lazy-init a psycopg2 connection pool for TimescaleDB."""
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    if not TIMESCALE_URL:
        return None
    try:
        import psycopg2
        import psycopg2.pool
        _pg_pool = psycopg2.pool.SimpleConnectionPool(1, 3, TIMESCALE_URL)
        return _pg_pool
    except Exception as e:
        # Import logger later — this runs at module level
        print(f"[WARN] TimescaleDB connection failed, metrics disabled: {e}", file=sys.stderr)
        return None


def _run_claude(cmd: list[str], timeout: int = 280) -> subprocess.CompletedProcess:
    """Run claude subprocess, killing the full process tree on timeout."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            cmd, proc.returncode, stdout, stderr,
        )
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise

from hcli_logging import get_logger, get_audit_logger

_shutdown = False

logger = get_logger(__name__, service="claude")
audit = get_audit_logger("claude")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
TASKS_KEY = "hcli:tasks"
RESULT_PREFIX = "hcli:results:"
RESULT_TTL = 600
SESSION_PREFIX = "hcli:session:"
SESSION_SIZE_PREFIX = "hcli:session_size:"
SESSION_HISTORY_PREFIX = "hcli:session_history:"
MEMORY_PREFIX = "hcli:memory:"
SESSION_TTL = int(os.environ.get("SESSION_TTL", "14400"))  # 4h
HISTORY_TTL = int(os.environ.get("HISTORY_TTL", "86400"))  # 24h
SESSION_CHUNK_DIR = "/var/log/hcli/sessions"
MAX_SESSION_BYTES = 100 * 1024  # 100KB

_CHAT_NAMES = {}
for _pair in os.environ.get("CHAT_NAMES", "").split(","):
    if ":" in _pair:
        _cid, _name = _pair.strip().split(":", 1)
        _CHAT_NAMES[_cid.strip()] = _name.strip()


def _chat_dir_name(chat_id) -> str:
    return _CHAT_NAMES.get(str(chat_id), str(chat_id))

RESULT_HMAC_KEY = os.environ.get("RESULT_HMAC_KEY", "")
if not RESULT_HMAC_KEY:
    raise RuntimeError("RESULT_HMAC_KEY not set — run install.sh to generate one")

GROUND_RULES_PATH = "/app/groundRules.md"
CONTEXT_PATH = "/app/context.md"
SKILLS_DIRS = ["/app/skills/public", "/app/skills/private"]
MAX_SKILLS_BYTES = 20 * 1024  # 20KB budget for injected skills


def _sign_result(task_id: str, output: str, completed_at: str) -> str:
    """HMAC-SHA256 sign a result to prevent spoofing via Redis."""
    msg = f"{task_id}:{output}:{completed_at}"
    return hmac.new(
        RESULT_HMAC_KEY.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()

def _load_base_prompt() -> str:
    """Load base system prompt from ground rules + user context files."""
    parts = []
    for path in (GROUND_RULES_PATH, CONTEXT_PATH):
        try:
            with open(path) as f:
                parts.append(f.read().strip())
        except FileNotFoundError:
            logger.debug("Prompt file not found, skipping: %s", path)
    if not parts:
        parts.append(
            "You are h-cli, a Telegram assistant. "
            "Use the available MCP tools to fulfill the user's request. "
            "Be concise. Return just the relevant output."
        )
    return "\n\n---\n\n".join(parts)

_BASE_PROMPT = _load_base_prompt()


def _load_matching_skills(message: str) -> str:
    """Load skill files whose keywords match the user's message.

    Each .md file in SKILLS_DIRS can have a YAML-style header:
        ---
        keywords: ospf, routing, area
        ---
    If no header, falls back to matching against the filename (sans .md).
    Returns concatenated content of matching skills, capped at MAX_SKILLS_BYTES.
    """
    msg_lower = message.lower()
    msg_words = set(re.findall(r"[a-z0-9][\w.-]*", msg_lower))

    matched = []
    for skills_dir in SKILLS_DIRS:
        if not os.path.isdir(skills_dir):
            continue
        try:
            entries = os.listdir(skills_dir)
        except OSError:
            continue

        for fname in sorted(entries):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(skills_dir, fname)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath) as f:
                    content = f.read()
            except OSError:
                continue

            # Parse YAML-style keywords header
            keywords = None
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    header = content[3:end]
                    for line in header.splitlines():
                        line = line.strip()
                        if line.lower().startswith("keywords:"):
                            kw_str = line.split(":", 1)[1].strip()
                            keywords = {k.strip().lower() for k in kw_str.split(",") if k.strip()}
                            break

            # No keywords header → skip (README.md etc. won't be loaded)
            if keywords is None:
                # Fall back to filename matching
                stem = fname[:-3].lower()
                if stem not in msg_words:
                    continue
            else:
                if not keywords & msg_words:
                    continue

            matched.append((fname, content))

    if not matched:
        return ""

    # Concatenate within budget
    parts = []
    total = 0
    for fname, content in matched:
        if total + len(content) > MAX_SKILLS_BYTES:
            remaining = MAX_SKILLS_BYTES - total
            if remaining > 200:  # only include if meaningful portion fits
                parts.append(content[:remaining] + "\n[...truncated]")
            break
        parts.append(content)
        total += len(content)

    names = [m[0] for m in matched[:len(parts)]]
    logger.info("Skills loaded: %s (%d bytes)", ", ".join(names), total)
    return "\n\n---\n\n".join(parts)


MAX_MEMORY_INJECT = 50 * 1024  # 50KB of most recent chunk (hybrid: full Redis + partial chunk)

_CHAT_ID_RE = re.compile(r"^-?\d+$")


def _validate_chat_id(chat_id) -> bool:
    """Validate chat_id is a numeric Telegram ID (no path traversal)."""
    return bool(_CHAT_ID_RE.match(str(chat_id)))


def _load_recent_chunks(chat_id) -> str:
    """Read session chunk files from disk and return their content."""
    if not _validate_chat_id(chat_id):
        logger.warning("Invalid chat_id for chunk load, skipping: %s", chat_id)
        return ""
    chunk_dir = os.path.join(SESSION_CHUNK_DIR, _chat_dir_name(chat_id))
    if not os.path.isdir(chunk_dir):
        return ""
    chunks = sorted(
        (f for f in os.listdir(chunk_dir) if f.startswith("chunk_")),
        reverse=True,
    )
    if not chunks:
        return ""
    content = ""
    for chunk_file in chunks:
        try:
            with open(os.path.join(chunk_dir, chunk_file)) as f:
                text = f.read()
            if len(content) + len(text) > MAX_MEMORY_INJECT:
                # Truncate to fit rather than skip entirely
                remaining = MAX_MEMORY_INJECT - len(content)
                if remaining > 0 and not content:
                    content = text[:remaining]
                break
            content = text + "\n\n" + content
        except OSError:
            continue
    return content.strip()


def _build_conversation_context(r: redis.Redis, chat_id) -> str:
    """Build recent conversation context from Redis session history."""
    history_key = f"{SESSION_HISTORY_PREFIX}{chat_id}"
    turns = r.lrange(history_key, 0, -1)
    if not turns:
        return ""
    lines = []
    for turn_json in turns:
        turn = json.loads(turn_json)
        role = turn.get("role", "unknown").upper()
        ts = datetime.fromtimestamp(
            turn.get("timestamp", 0), tz=timezone.utc
        ).strftime("%H:%M")
        content = turn.get("content", "")
        lines.append(f"[{ts}] **{role}**: {content}")
    return "\n\n".join(lines)


def build_system_prompt(chat_id=None, message: str = "") -> str:
    """Build per-task system prompt with session memory and skills injected."""
    prompt = _BASE_PROMPT
    if chat_id:
        memory = _load_recent_chunks(chat_id)
        if memory:
            prompt += (
                f"\n\n---\n\n## Previous Conversation History\n"
                f"The following is from previous sessions with this user. "
                f"Use it as context when the user references past interactions.\n\n"
                f"{memory}"
            )
    if message:
        skills = _load_matching_skills(message)
        if skills:
            prompt += (
                f"\n\n---\n\n## Relevant Skills\n"
                f"The following skill references are loaded because they match "
                f"the current message. Use them to inform your response.\n\n"
                f"{skills}"
            )
    return prompt

MCP_CONFIG = "/app/mcp-config.json"


def store_memory(r: redis.Redis, task_id: str, chat_id, role: str, content: str):
    """Store a conversation turn as raw JSON for future processing."""
    if not chat_id:
        return
    key = f"{MEMORY_PREFIX}{task_id}:{role}"
    doc = json.dumps({
        "chat_id": str(chat_id),
        "role": role,
        "content": content,
        "timestamp": time.time(),
        "task_id": task_id,
    })
    r.set(key, doc, ex=SESSION_TTL)


def dump_session_chunk(r: redis.Redis, chat_id: str, session_id: str) -> str | None:
    """Dump accumulated session history to a text file and clear Redis state."""
    if not _validate_chat_id(chat_id):
        logger.warning("Invalid chat_id for chunk dump, skipping: %s", chat_id)
        return None
    history_key = f"{SESSION_HISTORY_PREFIX}{chat_id}"
    turns = r.lrange(history_key, 0, -1)
    if not turns:
        return None

    chunk_dir = os.path.join(SESSION_CHUNK_DIR, _chat_dir_name(chat_id))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    chunk_path = os.path.join(chunk_dir, f"chunk_{timestamp}.txt")

    try:
        os.makedirs(chunk_dir, exist_ok=True)
        with open(chunk_path, "w") as f:
            f.write(f"=== h-cli session chunk ===\n")
            f.write(f"Chat: {chat_id}\n")
            f.write(f"Session: {session_id}\n")
            f.write(f"Chunked: {timestamp}\n")
            f.write(f"Turns: {len(turns)}\n")
            f.write(f"===\n\n")
            for turn_json in turns:
                turn = json.loads(turn_json)
                role = turn.get("role", "unknown").upper()
                ts = datetime.fromtimestamp(
                    turn.get("timestamp", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
                content = turn.get("content", "")
                f.write(f"[{ts}] {role}:\n{content}\n\n---\n\n")
    except OSError as e:
        logger.error("Failed to write session chunk %s: %s", chunk_path, e)
        return None

    # Only clear Redis state after successful file write
    r.delete(history_key)
    r.delete(f"{SESSION_SIZE_PREFIX}{chat_id}")

    logger.info("Session chunk saved: %s (%d turns)", chunk_path, len(turns))
    return chunk_path


STATS_KEY_PREFIX = "hcli:stats:"
STATS_TTL = 86400  # 24h


def _write_metrics(
    r: redis.Redis,
    task_id: str,
    chat_id,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_create: int,
    cost_usd: float,
    duration_ms: int,
    num_turns: int,
    is_error: bool,
) -> None:
    """Write task metrics to TimescaleDB and Redis counters."""
    now = datetime.now(timezone.utc)

    # ── Redis counters (always, for /stats) ────────────────────────────
    date_key = f"{STATS_KEY_PREFIX}{now.strftime('%Y-%m-%d')}"
    pipe = r.pipeline()
    pipe.hincrby(date_key, "tasks", 1)
    pipe.hincrby(date_key, "input_tokens", input_tokens)
    pipe.hincrby(date_key, "output_tokens", output_tokens)
    pipe.hincrby(date_key, "cache_read", cache_read)
    pipe.hincrby(date_key, "cache_create", cache_create)
    pipe.hincrbyfloat(date_key, "cost_usd", cost_usd)
    pipe.hincrby(date_key, "duration_ms", duration_ms)
    pipe.hincrby(date_key, "num_turns", num_turns)
    if is_error:
        pipe.hincrby(date_key, "errors", 1)
    pipe.expire(date_key, STATS_TTL)
    pipe.execute()

    # ── TimescaleDB (if available) ─────────────────────────────────────
    pool = _get_pg_pool()
    if pool is None:
        return
    conn = None
    try:
        conn = pool.getconn()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO task_metrics
                   (time, task_id, chat_id, model, input_tokens, output_tokens,
                    cache_read, cache_create, cost_usd, duration_ms, num_turns, is_error)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (now, task_id, str(chat_id) if chat_id else None, model,
                 input_tokens, output_tokens, cache_read, cache_create,
                 cost_usd, duration_ms, num_turns, is_error),
            )
        conn.commit()
    except Exception as e:
        logger.warning("TimescaleDB write failed (non-fatal): %s", e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            try:
                pool.putconn(conn)
            except Exception:
                pass


def process_task(r: redis.Redis, task_json: str) -> None:
    """Parse a task, invoke claude -p with session continuity, store the result."""
    try:
        task = json.loads(task_json)
    except json.JSONDecodeError:
        logger.error("Malformed task JSON, skipping: %s", task_json[:200])
        return
    task_id = task.get("task_id")
    if not task_id or not isinstance(task_id, str) or len(task_id) > 100:
        logger.error("Invalid or missing task_id, skipping: %s", task_json[:200])
        return
    message = task.get("message", task.get("command", ""))
    user_id = task.get("user_id", "unknown")
    chat_id = task.get("chat_id")

    logger.info("Processing task %s: %s", task_id, message)
    audit.info(
        "task_started",
        extra={"task_id": task_id, "user_message": message, "user_id": user_id},
    )

    # ── Session expiry recovery — dump history before starting fresh ──
    if chat_id:
        if not r.exists(f"{SESSION_PREFIX}{chat_id}"):
            history_key = f"{SESSION_HISTORY_PREFIX}{chat_id}"
            if r.llen(history_key) > 0:
                chunk_path = dump_session_chunk(r, str(chat_id), "expired")
                if chunk_path:
                    logger.info(
                        "Saved expired session history for chat %s -> %s",
                        chat_id, chunk_path,
                    )

    # ── Session chunking — rotate if accumulated size > 100KB ─────────
    if chat_id:
        size_key = f"{SESSION_SIZE_PREFIX}{chat_id}"
        current_size = int(r.get(size_key) or 0)
        if current_size > MAX_SESSION_BYTES:
            old_session = r.get(f"{SESSION_PREFIX}{chat_id}")
            if old_session:
                chunk_path = dump_session_chunk(
                    r, str(chat_id), old_session,
                )
                r.delete(f"{SESSION_PREFIX}{chat_id}")
                if chunk_path:
                    logger.info(
                        "Session for chat %s chunked at %d bytes -> %s",
                        chat_id, current_size, chunk_path,
                    )
                    app_path = chunk_path
                    message = (
                        f"[Context: previous conversation was chunked to "
                        f"{app_path} due to size. Read it if you need "
                        f"prior context.]\n\n{message}"
                    )

    # ── Session management ────────────────────────────────────────────
    session_key = f"{SESSION_PREFIX}{chat_id}" if chat_id else None
    session_id = str(uuid.uuid4())
    logger.info("Session %s for chat %s", session_id, chat_id)

    # ── Prepend recent conversation to message ────────────────────────
    original_message = message  # preserve for history storage
    if chat_id:
        recent = _build_conversation_context(r, chat_id)
        if recent:
            now = datetime.now(timezone.utc).strftime("%H:%M")
            message = (
                f"## Recent conversation (same session)\n"
                f"Below is your conversation history with this user. "
                f"Lines marked ASSISTANT are YOUR previous replies.\n\n"
                f"{recent}\n---\n\n"
                f"[{now}] **USER**: {{{message}}}"
            )

    # ── Build command ─────────────────────────────────────────────────
    system_prompt = build_system_prompt(chat_id, original_message)
    # Map logical model names from bot to actual model IDs via env vars
    _model_map = {
        "opus": os.environ.get("MAIN_MODEL", "opus"),
        "sonnet": os.environ.get("MAIN_MODEL", "opus"),
        "haiku": os.environ.get("FAST_MODEL", "haiku"),
    }
    raw_model = task.get("model", "opus")
    task_model = _model_map.get(raw_model, raw_model)
    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--mcp-config", MCP_CONFIG,
        "--allowedTools", "mcp__h-cli-core__run_command,mcp__h-cli-memory__memory_search",
        "--model", task_model,
        "--system-prompt", system_prompt,
        "--session-id", session_id,
        "--", message,
    ]

    metrics = {}
    try:
        proc = _run_claude(cmd)
        raw_out = proc.stdout.strip()
        if proc.stderr:
            logger.debug("claude stderr: %s", proc.stderr.strip())

        # Parse JSON response
        if raw_out:
            try:
                result_json = json.loads(raw_out)
                output = result_json.get("result", "")
                if not output and result_json.get("is_error"):
                    output = "; ".join(result_json.get("errors", ["(error, no output)"]))
                usage = result_json.get("usage", {})
                model_usage = result_json.get("modelUsage", {})
                # Extract model name from modelUsage keys
                model_name = next(iter(model_usage), task_model)
                metrics = {
                    "model": model_name,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_read": usage.get("cache_read_input_tokens", 0),
                    "cache_create": usage.get("cache_creation_input_tokens", 0),
                    "cost_usd": result_json.get("total_cost_usd", 0) or 0,
                    "duration_ms": result_json.get("duration_ms", 0) or 0,
                    "num_turns": result_json.get("num_turns", 1) or 1,
                    "is_error": result_json.get("is_error", False),
                }
            except json.JSONDecodeError:
                logger.warning("Failed to parse claude JSON output, using raw text")
                output = raw_out

        if not output:
            output = proc.stderr.strip() or "(no output from Claude)"

    except subprocess.TimeoutExpired:
        output = "Error: Claude Code timed out after 280 seconds"
        metrics = {"is_error": True}
        logger.warning("Task %s timed out", task_id)
    except Exception as e:
        output = f"Error: {e}"
        metrics = {"is_error": True}
        logger.exception("Task %s failed", task_id)

    # ── Persist session ID for reuse ──────────────────────────────────
    if session_key:
        r.set(session_key, session_id, ex=SESSION_TTL)

    # ── Track session size and history for chunking ───────────────────
    if chat_id:
        exchange_size = len(original_message) + len(output)
        size_key = f"{SESSION_SIZE_PREFIX}{chat_id}"
        r.incrby(size_key, exchange_size)
        r.expire(size_key, HISTORY_TTL)

        history_key = f"{SESSION_HISTORY_PREFIX}{chat_id}"
        turn_user = json.dumps({
            "role": "user", "content": original_message, "timestamp": time.time(),
        })
        turn_asst = json.dumps({
            "role": "assistant", "content": output, "timestamp": time.time(),
        })
        r.rpush(history_key, turn_user, turn_asst)
        r.expire(history_key, HISTORY_TTL)

    # ── Write metrics to TimescaleDB + Redis counters ────────────────
    if metrics:
        try:
            _write_metrics(
                r,
                task_id=task_id,
                chat_id=chat_id,
                model=metrics.get("model", task_model),
                input_tokens=metrics.get("input_tokens", 0),
                output_tokens=metrics.get("output_tokens", 0),
                cache_read=metrics.get("cache_read", 0),
                cache_create=metrics.get("cache_create", 0),
                cost_usd=metrics.get("cost_usd", 0),
                duration_ms=metrics.get("duration_ms", 0),
                num_turns=metrics.get("num_turns", 1),
                is_error=metrics.get("is_error", False),
            )
        except Exception:
            logger.exception("Failed to write metrics for task %s", task_id)

    # ── Store raw conversation for future memory processing ───────────
    store_memory(r, task_id, chat_id, "user", original_message)
    store_memory(r, task_id, chat_id, "asst", output)

    completed_at = datetime.now(timezone.utc).isoformat()

    # Build result with optional usage info for the bot
    result_data = {
        "output": output,
        "completed_at": completed_at,
    }
    if metrics and not metrics.get("is_error"):
        result_data["usage"] = {
            "model": metrics.get("model", task_model),
            "input_tokens": metrics.get("input_tokens", 0),
            "output_tokens": metrics.get("output_tokens", 0),
            "cost_usd": metrics.get("cost_usd", 0),
            "duration_ms": metrics.get("duration_ms", 0),
        }
    result_data["hmac"] = _sign_result(task_id, output, completed_at)

    r.set(f"{RESULT_PREFIX}{task_id}", json.dumps(result_data), ex=RESULT_TTL)

    audit.info(
        "task_completed",
        extra={
            "task_id": task_id,
            "output_length": len(output),
            "output": output,
        },
    )
    logger.info("Task %s completed (%d chars)", task_id, len(output))


def _handle_sigterm(signum, frame):
    global _shutdown
    logger.info("Received SIGTERM, finishing current task...")
    _shutdown = True


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)

    logger.info("Connecting to Redis at %s", REDIS_URL.split("@")[-1])
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    logger.info("Redis connected. Waiting for tasks on %s...", TASKS_KEY)

    heartbeat_path = "/tmp/heartbeat"

    while not _shutdown:
        try:
            # Touch heartbeat so Docker healthcheck can verify liveness
            open(heartbeat_path, "w").close()

            result = r.blpop(TASKS_KEY, timeout=30)
            if result is None:
                continue
            _, task_json = result
            process_task(r, task_json)
        except redis.ConnectionError:
            logger.error("Redis connection lost, reconnecting in 5s...")
            time.sleep(5)
            r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        except KeyboardInterrupt:
            logger.info("Dispatcher shutting down")
            break
        except Exception:
            logger.exception("Unexpected error in dispatch loop")

    logger.info("Dispatcher stopped")


if __name__ == "__main__":
    main()
