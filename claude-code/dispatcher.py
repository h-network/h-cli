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

ALLOWED_MODELS = {"haiku", "sonnet", "opus"}


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
        raw_role = turn.get("role", "unknown").lower()
        role = "YOU" if raw_role == "assistant" else "USER"
        ts = datetime.fromtimestamp(
            turn.get("timestamp", 0), tz=timezone.utc
        ).strftime("%H:%M")
        content = turn.get("content", "")
        lines.append(f"[{ts}] {role}: {content}")
    return "\n\n".join(lines)


def build_system_prompt(chat_id=None) -> str:
    """Build per-task system prompt with session memory injected."""
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

    # ── Model selection (from Telegram toggle, default haiku) ─────────
    model = task.get("model", "haiku")
    if model not in ALLOWED_MODELS:
        logger.warning("Invalid model '%s' in task %s, falling back to haiku", model, task_id)
        model = "haiku"

    logger.info("Processing task %s (model=%s): %s", task_id, model, message)
    audit.info(
        "task_started",
        extra={"task_id": task_id, "user_message": message, "user_id": user_id, "model": model},
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

    original_message = message  # preserve for history storage

    # ── Build command ─────────────────────────────────────────────────
    system_prompt = build_system_prompt(chat_id)
    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--mcp-config", MCP_CONFIG,
        "--allowedTools", "mcp__h-cli-core__run_command,mcp__h-cli-memory__memory_search",
        "--model", model,
        "--system-prompt", system_prompt,
        "--", message,
    ]

    usage_stats = None
    try:
        proc = _run_claude(cmd)
        raw_output = proc.stdout.strip()
        if proc.stderr:
            logger.debug("claude stderr: %s", proc.stderr.strip())
        if not raw_output:
            output = proc.stderr.strip() or "(no output from Claude)"
        else:
            try:
                result_json = json.loads(raw_output)
                output = result_json.get("result", raw_output)
                usage = result_json.get("usage", {})
                input_tokens = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                cost = result_json.get("total_cost_usd", 0)
                duration = result_json.get("duration_ms", 0)
                usage_stats = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost,
                    "duration_ms": duration,
                    "model": model,
                }
            except (json.JSONDecodeError, KeyError):
                output = raw_output

    except subprocess.TimeoutExpired:
        output = "Error: Claude Code timed out after 280 seconds"
        logger.warning("Task %s timed out", task_id)
    except Exception as e:
        output = f"Error: {e}"
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

    # ── Store raw conversation for future memory processing ───────────
    store_memory(r, task_id, chat_id, "user", original_message)
    store_memory(r, task_id, chat_id, "asst", output)

    completed_at = datetime.now(timezone.utc).isoformat()
    result_payload = {
        "output": output,
        "completed_at": completed_at,
        "hmac": _sign_result(task_id, output, completed_at),
    }
    if usage_stats:
        result_payload["usage"] = usage_stats
    result = json.dumps(result_payload)

    r.set(f"{RESULT_PREFIX}{task_id}", result, ex=RESULT_TTL)

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
