"""h-cli Claude Code dispatcher — BLPOP loop that invokes claude -p per task."""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import redis

from hbot_logging import get_logger, get_audit_logger

logger = get_logger(__name__, service="claude")
audit = get_audit_logger("claude")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
TASKS_KEY = "hcli:tasks"
RESULT_PREFIX = "hcli:results:"
RESULT_TTL = 600
SESSION_PREFIX = "hcli:session:"
MEMORY_PREFIX = "hcli:memory:"
SESSION_TTL = int(os.environ.get("SESSION_TTL", "14400"))  # 4h

SYSTEM_PROMPT = (
    "You are h-cli, a network operations assistant. "
    "Use the available MCP tools to fulfill the user's request. "
    "Be concise. Return just the relevant output."
)

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
    r.set(key, doc)


def process_task(r: redis.Redis, task_json: str) -> None:
    """Parse a task, invoke claude -p with session continuity, store the result."""
    task = json.loads(task_json)
    task_id = task["task_id"]
    message = task.get("message", task.get("command", ""))
    user_id = task.get("user_id", "unknown")
    chat_id = task.get("chat_id")

    logger.info("Processing task %s: %s", task_id, message)
    audit.info(
        "task_started",
        extra={"task_id": task_id, "user_message": message, "user_id": user_id},
    )

    # ── Session management ────────────────────────────────────────────
    session_key = f"{SESSION_PREFIX}{chat_id}" if chat_id else None
    existing_session = r.get(session_key) if session_key else None
    is_resume = existing_session is not None

    if is_resume:
        session_id = existing_session
        session_flags = ["--resume", session_id]
        logger.info("Resuming session %s for chat %s", session_id, chat_id)
    else:
        session_id = str(uuid.uuid4())
        session_flags = ["--session-id", session_id]
        logger.info("New session %s for chat %s", session_id, chat_id)

    # ── Build command ─────────────────────────────────────────────────
    cmd = [
        "claude",
        "-p", message,
        "--mcp-config", MCP_CONFIG,
        "--allowedTools", "mcp__h-cli-core__run_command",
        "--model", "sonnet",
        "--system-prompt", SYSTEM_PROMPT,
    ] + session_flags

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=290,
        )
        output = proc.stdout.strip()
        if proc.stderr:
            logger.debug("claude stderr: %s", proc.stderr.strip())
        if not output:
            output = proc.stderr.strip() or "(no output from Claude)"

        # If --resume failed, retry with a fresh session
        if is_resume and proc.returncode != 0:
            logger.warning(
                "Resume failed (rc=%d) for session %s, retrying fresh",
                proc.returncode, session_id,
            )
            session_id = str(uuid.uuid4())
            cmd_retry = [
                "claude",
                "-p", message,
                "--mcp-config", MCP_CONFIG,
                "--allowedTools", "mcp__h-cli-core__run_command",
                "--model", "sonnet",
                "--system-prompt", SYSTEM_PROMPT,
                "--session-id", session_id,
            ]
            proc = subprocess.run(
                cmd_retry,
                capture_output=True,
                text=True,
                timeout=290,
            )
            output = proc.stdout.strip()
            if proc.stderr:
                logger.debug("claude stderr (retry): %s", proc.stderr.strip())
            if not output:
                output = proc.stderr.strip() or "(no output from Claude)"

    except subprocess.TimeoutExpired:
        output = "Error: Claude Code timed out after 290 seconds"
        logger.warning("Task %s timed out", task_id)
    except Exception as e:
        output = f"Error: {e}"
        logger.exception("Task %s failed", task_id)

    # ── Persist session for future resume ─────────────────────────────
    if session_key:
        r.set(session_key, session_id, ex=SESSION_TTL)

    # ── Store raw conversation for future memory processing ───────────
    store_memory(r, task_id, chat_id, "user", message)
    store_memory(r, task_id, chat_id, "asst", output)

    result = json.dumps({
        "output": output,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })

    r.set(f"{RESULT_PREFIX}{task_id}", result, ex=RESULT_TTL)

    audit.info(
        "task_completed",
        extra={
            "task_id": task_id,
            "output_length": len(output),
        },
    )
    logger.info("Task %s completed (%d chars)", task_id, len(output))


def main() -> None:
    logger.info("Connecting to Redis at %s", REDIS_URL)
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    logger.info("Redis connected. Waiting for tasks on %s...", TASKS_KEY)

    while True:
        try:
            result = r.blpop(TASKS_KEY, timeout=0)
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


if __name__ == "__main__":
    main()
