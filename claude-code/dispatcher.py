"""h-cli Claude Code dispatcher — BLPOP loop that invokes claude -p per task."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import redis

from hbot_logging import get_logger, get_audit_logger

logger = get_logger(__name__, service="claude")
audit = get_audit_logger("claude")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
TASKS_KEY = "hcli:tasks"
RESULT_PREFIX = "hcli:results:"
RESULT_TTL = 600

SYSTEM_PROMPT = (
    "You are h-cli, a network operations assistant. "
    "Use the available MCP tools to fulfill the user's request. "
    "Be concise. Return just the relevant output."
)

MCP_CONFIG = "/app/mcp-config.json"


def process_task(r: redis.Redis, task_json: str) -> None:
    """Parse a task, invoke claude -p, store the result."""
    task = json.loads(task_json)
    task_id = task["task_id"]
    message = task.get("message", task.get("command", ""))
    user_id = task.get("user_id", "unknown")

    logger.info("Processing task %s: %s", task_id, message)
    audit.info(
        "task_started",
        extra={"task_id": task_id, "user_message": message, "user_id": user_id},
    )

    cmd = [
        "claude",
        "-p", message,
        "--mcp-config", MCP_CONFIG,
        "--allowedTools", "mcp__h-cli-core__run_command",
        "--model", "sonnet",
        "--system-prompt", SYSTEM_PROMPT,
    ]

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
    except subprocess.TimeoutExpired:
        output = "Error: Claude Code timed out after 290 seconds"
        logger.warning("Task %s timed out", task_id)
    except Exception as e:
        output = f"Error: {e}"
        logger.exception("Task %s failed", task_id)

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
            import time
            time.sleep(5)
            r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        except KeyboardInterrupt:
            logger.info("Dispatcher shutting down")
            break
        except Exception:
            logger.exception("Unexpected error in dispatch loop")


if __name__ == "__main__":
    main()
