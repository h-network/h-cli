"""h-cli Telegram Bot — async command interface with Redis task queue."""

import asyncio
import functools
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from telegram import Update
from telegram.error import BadRequest
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
REDIS_PENDING_PREFIX = "hcli:pending:"
SESSION_HISTORY_PREFIX = "hcli:session_history:"
SESSION_SIZE_PREFIX = "hcli:session_size:"
SESSION_CHUNK_DIR = os.environ.get("SESSION_CHUNK_DIR", "/var/log/hcli/sessions")
POLL_INTERVAL = 1  # seconds
_background_tasks: set[asyncio.Task] = set()  # prevent GC of fire-and-forget tasks


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


def markdown_to_telegram_html(text: str) -> str:
    """Convert markdown to Telegram-supported HTML.

    Extracts protected blocks (code, inline code, tables) into placeholders
    before processing inline markdown, then restores them at the end.
    """
    placeholders: list[str] = []

    def _placeholder(content: str) -> str:
        idx = len(placeholders)
        placeholders.append(content)
        return f"\x00PH{idx}\x00"

    # 1. Extract fenced code blocks
    def _code_block(m: re.Match) -> str:
        code = m.group(2)
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return _placeholder(f"<pre>{escaped}</pre>")

    text = re.sub(r"```(\w*)\n?(.*?)```", _code_block, text, flags=re.DOTALL)

    # 2. Extract inline code
    def _inline_code(m: re.Match) -> str:
        code = m.group(1)
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return _placeholder(f"<code>{escaped}</code>")

    text = re.sub(r"`([^`]+)`", _inline_code, text)

    # 3. Extract tables (consecutive lines starting with |)
    def _table_block(m: re.Match) -> str:
        lines = m.group(0).strip().split("\n")
        cleaned = []
        for line in lines:
            # Skip separator rows like |---|---|
            if re.match(r"^\|[\s\-:|]+\|$", line):
                continue
            # Strip leading/trailing pipes and clean cells
            cells = [c.strip() for c in line.strip("|").split("|")]
            cleaned.append(" | ".join(cells))
        table_text = "\n".join(cleaned)
        escaped = (
            table_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return _placeholder(f"<pre>{escaped}</pre>")

    text = re.sub(r"(?:^\|.+\|$\n?)+", _table_block, text, flags=re.MULTILINE)

    # 4. Escape HTML entities in remaining text
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 5. Markdown links [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )

    # 6. Bold **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # 7. Italic *text* (but not inside words like file*name)
    text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)

    # 8. Headers # ... (strip hashes, make bold)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 9. Bullet lists (- item or * item at line start)
    text = re.sub(r"^[\-\*]\s+", "  \u2022 ", text, flags=re.MULTILINE)

    # 10. Strip horizontal rules
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)

    # 11. Restore placeholders
    for idx, content in enumerate(placeholders):
        text = text.replace(f"\x00PH{idx}\x00", content)

    return text.strip()


async def send_long(update: Update, text: str) -> None:
    """Send text as HTML, splitting at Telegram's 4096-char limit on line boundaries."""
    html = markdown_to_telegram_html(text)
    while html:
        if len(html) <= TELEGRAM_MAX_LEN:
            chunk = html
            html = ""
        else:
            split_at = html.rfind('\n', 0, TELEGRAM_MAX_LEN)
            if split_at == -1:
                split_at = TELEGRAM_MAX_LEN
            chunk = html[:split_at]
            html = html[split_at:].lstrip('\n')
        try:
            await update.message.reply_text(chunk, parse_mode="HTML")
        except BadRequest as e:
            logger.warning("HTML parse failed, falling back to plain text: %s", e)
            await update.message.reply_text(chunk)


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
        "/cancel — Cancel the last queued task\n"
        "/status — Show task queue depth\n"
        "/help   — This message"
    )


@auth_required
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = _redis(context)
    depth = await r.llen(REDIS_TASKS_KEY)
    await update.message.reply_text(f"Tasks in queue: {depth}")


async def _dump_session_chunk(r: aioredis.Redis, chat_id: int) -> str | None:
    """Dump session history from Redis to a chunk file on disk."""
    history_key = f"{SESSION_HISTORY_PREFIX}{chat_id}"
    turns = await r.lrange(history_key, 0, -1)
    if not turns:
        return None

    chunk_dir = os.path.join(SESSION_CHUNK_DIR, str(chat_id))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    chunk_path = os.path.join(chunk_dir, f"chunk_{timestamp}.txt")

    try:
        os.makedirs(chunk_dir, exist_ok=True)
        with open(chunk_path, "w") as f:
            f.write("=== h-cli session chunk ===\n")
            f.write(f"Chat: {chat_id}\n")
            f.write(f"Session: /new\n")
            f.write(f"Chunked: {timestamp}\n")
            f.write(f"Turns: {len(turns)}\n")
            f.write("===\n\n")
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

    await r.delete(history_key)
    await r.delete(f"{SESSION_SIZE_PREFIX}{chat_id}")
    logger.info("Session chunk saved: %s (%d turns)", chunk_path, len(turns))
    return chunk_path


@auth_required
async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dump session history to a chunk file, then clear the session."""
    r = _redis(context)
    chat_id = update.effective_chat.id
    chunk_path = await _dump_session_chunk(r, chat_id)
    await r.delete(f"hcli:session:{chat_id}")
    if chunk_path:
        await update.message.reply_text(
            f"Session saved to {os.path.basename(chunk_path)}. "
            "Context cleared — next message starts fresh."
        )
    else:
        await update.message.reply_text("Context cleared. Next message starts fresh.")


@auth_required
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the most recent pending/in-flight task for this chat."""
    r = _redis(context)
    chat_id = update.effective_chat.id
    pending_key = f"{REDIS_PENDING_PREFIX}{chat_id}"

    # Pop the most recent pending task for this chat
    task_id = await r.rpop(pending_key)
    if not task_id:
        await update.message.reply_text("No queued tasks to cancel.")
        return

    # Try to remove from the dispatch queue (may already be picked up)
    tasks = await r.lrange(REDIS_TASKS_KEY, 0, -1)
    for raw_task in reversed(tasks):
        try:
            task = json.loads(raw_task)
        except json.JSONDecodeError:
            continue
        if task.get("task_id") == task_id:
            await r.lrem(REDIS_TASKS_KEY, -1, raw_task)
            break

    # Write a signed cancellation result so _poll_result picks it up naturally
    output = "Task cancelled."
    completed_at = datetime.now(timezone.utc).isoformat()
    msg = f"{task_id}:{output}:{completed_at}"
    sig = hmac.new(
        RESULT_HMAC_KEY.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    result = json.dumps({
        "output": output,
        "completed_at": completed_at,
        "hmac": sig,
    })
    await r.set(f"{REDIS_RESULT_PREFIX}{task_id}", result, ex=TASK_TIMEOUT)

    audit.info(
        "task_cancelled",
        extra={"user_id": update.effective_user.id, "task_id": task_id},
    )
    await update.message.reply_text(f"Cancelled task `{task_id[:8]}`.")


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
    pending_key = f"{REDIS_PENDING_PREFIX}{update.effective_chat.id}"
    await r.rpush(pending_key, task_id)
    await r.expire(pending_key, TASK_TIMEOUT * 2)
    audit.info(
        "task_queued",
        extra={"user_id": uid, "task_id": task_id, "user_message": message},
    )
    logger.info("Task queued: %s (id=%s)", message, task_id)

    task = asyncio.create_task(_poll_result(update, r, task_id, uid))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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

    pending_key = f"{REDIS_PENDING_PREFIX}{update.effective_chat.id}"
    result_key = f"{REDIS_RESULT_PREFIX}{task_id}"
    for i in range(TASK_TIMEOUT):
        raw = await r.get(result_key)
        if raw is not None:
            await r.delete(result_key)
            await r.lrem(pending_key, 1, task_id)
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

    await r.lrem(pending_key, 1, task_id)
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
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Telegram bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
