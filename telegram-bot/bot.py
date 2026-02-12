"""h-cli Telegram Bot — async command interface with Redis task queue."""

import asyncio
import functools
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from hcli_logging import get_logger, get_audit_logger

logger = get_logger(__name__, service="telegram")
audit = get_audit_logger("telegram")

# ── Config ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")
MAX_CONCURRENT_TASKS = int(os.environ.get("MAX_CONCURRENT_TASKS", "3"))
TASK_TIMEOUT = int(os.environ.get("TASK_TIMEOUT", "300"))
ALLOWED_CHATS: set[int] = set()

_raw = os.environ.get("ALLOWED_CHATS", "")
if _raw.strip():
    for cid in _raw.split(","):
        cid = cid.strip()
        if not cid:
            continue
        try:
            ALLOWED_CHATS.add(int(cid))
        except ValueError:
            logger.warning("Invalid chat ID in ALLOWED_CHATS, skipping: %s", cid)

if not ALLOWED_CHATS:
    logger.warning(
        "ALLOWED_CHATS is empty — no users are authorized. "
        "The bot will reject all messages."
    )

RESULT_HMAC_KEY = os.environ.get("RESULT_HMAC_KEY", "")
if not RESULT_HMAC_KEY:
    raise RuntimeError("RESULT_HMAC_KEY not set — run install.sh to generate one")

TELEGRAM_MAX_LEN = 4096
REDIS_TASKS_KEY = "hcli:tasks"
REDIS_RESULT_PREFIX = "hcli:results:"
POLL_INTERVAL = 1  # seconds


def _verify_result(task_id: str, result: dict) -> bool:
    """Verify HMAC-SHA256 signature on a task result."""
    expected = result.get("hmac", "")
    msg = f"{task_id}:{result.get('output', '')}:{result.get('completed_at', '')}"
    computed = hmac.new(
        RESULT_HMAC_KEY.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, computed)


# ── Helpers ──────────────────────────────────────────────────────────────
def authorized(chat_id: int) -> bool:
    """Fail-closed: empty allowlist means nobody gets in."""
    return chat_id in ALLOWED_CHATS


async def send_long(update: Update, text: str) -> None:
    """Send text, splitting at Telegram's 4096-char limit on line boundaries."""
    while text:
        if len(text) <= TELEGRAM_MAX_LEN:
            await update.message.reply_text(text)
            break
        split_at = text.rfind('\n', 0, TELEGRAM_MAX_LEN)
        if split_at == -1:
            split_at = TELEGRAM_MAX_LEN
        await update.message.reply_text(text[:split_at])
        text = text[split_at:].lstrip('\n')


def _redis(context: ContextTypes.DEFAULT_TYPE) -> aioredis.Redis:
    return context.bot_data["redis"]


# ── Auth wrapper ─────────────────────────────────────────────────────────
def auth_required(handler):
    """Decorator that checks ALLOWED_CHATS before running the handler."""
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not authorized(chat_id):
            logger.warning("Unauthorized access attempt", extra={
                "chat_id": chat_id,
                "user_id": update.effective_user.id,
            })
            await update.message.reply_text("Not authorized.")
            return
        return await handler(update, context)
    return wrapper


# ── Command handlers ─────────────────────────────────────────────────────
@auth_required
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "h-cli bot ready.\n"
        "Use /help to see available commands."
    )


@auth_required
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send any message in natural language and I'll figure out the right tool.\n\n"
        "/run <command> — Execute a shell command directly\n"
        "/new    — Clear context, start a fresh conversation\n"
        "/status — Show task queue depth\n"
        "/help   — This message"
    )


@auth_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = _redis(context)
    depth = await r.llen(REDIS_TASKS_KEY)
    await update.message.reply_text(f"Tasks in queue: {depth}")


@auth_required
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the current session so the next message starts fresh."""
    r = _redis(context)
    chat_id = update.effective_chat.id
    await r.delete(f"hcli:session:{chat_id}")
    await update.message.reply_text("Context cleared. Next message starts fresh.")


async def _queue_task(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message: str,
) -> None:
    """Check concurrency, queue task to Redis, poll for result."""
    r = _redis(context)
    uid = update.effective_user.id

    depth = await r.llen(REDIS_TASKS_KEY)
    if depth >= MAX_CONCURRENT_TASKS:
        await update.message.reply_text(
            f"Queue full ({depth}/{MAX_CONCURRENT_TASKS}). Try again later."
        )
        return

    task_id = str(uuid.uuid4())
    task = json.dumps({
        "task_id": task_id,
        "message": message,
        "user_id": uid,
        "chat_id": update.effective_chat.id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })

    await r.rpush(REDIS_TASKS_KEY, task)
    audit.info(
        "task_queued",
        extra={"user_id": uid, "task_id": task_id, "user_message": message},
    )
    logger.info("Task queued: %s (id=%s)", message, task_id)

    await _poll_result(update, r, task_id, uid)


@auth_required
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = " ".join(context.args) if context.args else ""
    if not command:
        await update.message.reply_text("Usage: /run <command>")
        return
    await _queue_task(update, context, command)


async def _poll_result(
    update: Update, r: aioredis.Redis, task_id: str, uid: int
) -> None:
    """Poll Redis for a task result, send it back to the user."""
    await update.message.reply_text(f"Queued task `{task_id[:8]}`...\nPolling for result...")

    result_key = f"{REDIS_RESULT_PREFIX}{task_id}"
    for i in range(TASK_TIMEOUT):
        raw = await r.get(result_key)
        if raw is not None:
            await r.delete(result_key)
            try:
                result = json.loads(raw)
                if not _verify_result(task_id, result):
                    logger.warning("HMAC verification failed for task %s", task_id)
                    audit.warning(
                        "hmac_failed",
                        extra={"task_id": task_id},
                    )
                    output = "(error: result integrity check failed)"
                else:
                    output = result.get("output", "(no output)")
            except json.JSONDecodeError:
                output = "(error: malformed result)"
            await send_long(update, output)
            audit.info(
                "task_completed",
                extra={"user_id": uid, "task_id": task_id},
            )
            return
        if i % 5 == 0:
            await update.effective_chat.send_action("typing")
        await asyncio.sleep(POLL_INTERVAL)

    await update.message.reply_text(
        f"Task `{task_id[:8]}` timed out after {TASK_TIMEOUT}s."
    )
    audit.info(
        "task_timeout",
        extra={"user_id": uid, "task_id": task_id, "timeout": TASK_TIMEOUT},
    )


@auth_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle natural language messages — queue to Redis for Claude Code."""
    message = update.message.text.strip()
    if not message:
        return
    await _queue_task(update, context, message)


# ── App lifecycle ────────────────────────────────────────────────────────
async def post_init(application: Application) -> None:
    pool = aioredis.ConnectionPool.from_url(
        REDIS_URL, decode_responses=True,
        socket_connect_timeout=5, socket_timeout=10,
    )
    application.bot_data["redis"] = aioredis.Redis(connection_pool=pool)
    application.bot_data["redis_pool"] = pool
    logger.info("Redis connection pool created (%s)", REDIS_URL.split("@")[-1])
    logger.info(
        "Bot started — allowed chats: %s, max tasks: %d, timeout: %ds",
        ALLOWED_CHATS or "(none)",
        MAX_CONCURRENT_TASKS,
        TASK_TIMEOUT,
    )


async def post_shutdown(application: Application) -> None:
    pool = application.bot_data.get("redis_pool")
    if pool:
        await pool.aclose()
        logger.info("Redis connection pool closed")


# ── Main ─────────────────────────────────────────────────────────────────
def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Telegram bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
