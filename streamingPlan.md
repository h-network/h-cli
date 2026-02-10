# Streaming Output: Implementation Plan

## Overview

Replace the current "fire and wait" pattern with real-time streaming from Claude Code to Telegram via Redis pub/sub.

**Before:** User sees silence for 10-280 seconds.
**After:** User sees live tool calls and text as Claude works.

## Flow

```
Dispatcher                    Redis Pub/Sub              Telegram Bot
    │                              │                          │
    ├─ Popen(claude -p             │                          │
    │   --output-format            │                          │
    │    stream-json --verbose)    │                          │
    │                              │                          │
    ├─ read stdout line by line ──►├─ PUBLISH                 │
    │   parse JSON events          │  hcli:stream:{task_id}   │
    │                              │◄─────── SUBSCRIBE ───────┤
    │                              │                          ├─ editMessageText()
    │                              │  {type, content}         │   (rate limited)
    │                              │                          │
    ├─ HMAC sign final result ────►├─ SET hcli:results:{id}   │
    │                              │                          ├─ delete stream msg
    └──────────────────────────────┘                          ├─ send_long(final)
                                                              └──
```

## Security Model

- Stream events are **unsigned** — purely UX, shown during processing only
- Final result is still **HMAC-verified** — replaces stream message at the end
- Pub/sub channel is ephemeral — auto-cleaned on disconnect
- Tool inputs/outputs truncated to 200 chars in stream (no secrets leaked in preview)

---

## File 1: `claude-code/dispatcher.py`

### Change 1: Rewrite `_run_claude()`

**Current:** Runs `proc.communicate(timeout=...)`, returns `CompletedProcess`.

**New signature:**
```python
def _run_claude(cmd: list[str], r: redis.Redis, task_id: str, timeout: int = 280) -> subprocess.CompletedProcess:
```

**Logic:**
1. Add `--output-format stream-json --verbose` to `cmd` before spawning
2. `Popen` with `stdout=PIPE, stderr=PIPE, text=True, start_new_session=True`
3. Set `deadline = time.monotonic() + timeout`
4. Read `proc.stdout` line by line in a loop:
   - Check `time.monotonic() > deadline` → kill + publish `{"type":"timeout"}` + raise `TimeoutExpired`
   - Parse each line as JSON with `_parse_stream_event()`
   - If event is not None, publish to `hcli:stream:{task_id}`
   - If event is a `result` type, capture the final text
   - Accumulate all text deltas into `full_output`
5. `proc.wait()` after stdout exhausted
6. Read any remaining stderr via `proc.stderr.read()`
7. Publish `{"type":"done"}` to the stream channel
8. Return `CompletedProcess(cmd, proc.returncode, full_output, stderr)`

**Timeout handling:**
```python
if time.monotonic() > deadline:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
    r.publish(f"hcli:stream:{task_id}", json.dumps({"type": "timeout"}))
    raise subprocess.TimeoutExpired(cmd, timeout)
```

### Change 2: Add `_parse_stream_event()` helper

```python
STREAM_TRUNCATE = 200
STREAM_PREFIX = "hcli:stream:"

def _parse_stream_event(line: str) -> dict | None:
```

**Parse the stream-json format:**

Each line from `claude -p --output-format stream-json --verbose` is a JSON object. Key event types:

| Event `type`    | What it means                    | What we emit                                    |
|-----------------|----------------------------------|-------------------------------------------------|
| `assistant`     | `content[].type == "text"`       | `{"type":"text", "content":"<delta>"}`          |
| `assistant`     | `content[].type == "tool_use"`   | `{"type":"tool", "name":"...", "input":"..."}`  |
| `content_block_delta` | text delta              | `{"type":"text", "content":"<delta>"}`          |
| `tool_result`   | tool output                      | `{"type":"tool_result", "content":"..."}`       |
| `result`        | final message                    | `{"type":"result", "content":"<final text>"}`   |

- Truncate `input` and `content` fields to 200 chars for pub/sub display
- Return `None` for unrecognized/irrelevant events (silently skip)
- Wrap in try/except — malformed lines return `None`

### Change 3: Update `process_task()`

Minimal change — just pass `r` and `task_id` to both `_run_claude()` calls:

```python
# Primary call
proc = _run_claude(cmd, r, task_id)

# Retry call (fresh session on resume failure)
proc = _run_claude(cmd_retry, r, task_id)
```

Everything else in `process_task()` stays the same (session management, HMAC signing, memory storage).

---

## File 2: `telegram-bot/bot.py`

### Change 1: Add `import time` and stream constants

```python
import time

STREAM_PREFIX = "hcli:stream:"
STREAM_EDIT_INTERVAL = 1.5  # seconds between Telegram message edits
STREAM_RESULT_WAIT = 30     # seconds to wait for HMAC result after stream ends
```

### Change 2: Rewrite `_poll_result()`

**Current:** Sends "Queued task..." then polls `hcli:results:{task_id}` every second for up to 300s.

**New logic:**

```python
async def _poll_result(
    update: Update, r: aioredis.Redis, task_id: str, uid: int
) -> None:
```

**Phase 1 — Stream:**
1. Send initial message: `status_msg = await update.message.reply_text("Working...")`
2. Create a **new Redis connection** for pub/sub (aioredis requires dedicated connection)
3. Subscribe to `hcli:stream:{task_id}`
4. Loop with `asyncio.wait_for(pubsub.get_message(), timeout=1.0)`:
   - Parse the JSON event
   - Format with `_format_stream_event()`
   - Append to `display_lines` (keep last ~15 lines to fit Telegram limit)
   - Rate-limit `status_msg.edit_text()` to every 1.5s
   - On `type=done` or `type=timeout`: break
5. Unsubscribe and close pub/sub connection
6. If no stream events received within TASK_TIMEOUT, fall through (backwards compat)

**Phase 2 — Final result (same as current, shorter timeout):**
7. Poll `hcli:results:{task_id}` for up to 30 seconds
8. Verify HMAC signature
9. Delete the streaming `status_msg`
10. Send clean final result via `send_long()`

**Error handling:**
- If Telegram `edit_text()` fails (rate limit, message too old), log and skip — non-fatal
- If stream never arrives, fall back to polling-only (graceful degradation)

### Change 3: Add `_format_stream_event()` helper

```python
def _format_stream_event(event: dict) -> str | None:
```

| Event type    | Display format                                           |
|---------------|----------------------------------------------------------|
| `text`        | The text content itself                                  |
| `tool`        | `→ {name}: {input[:100]}`                                |
| `tool_result` | `  ✓ {content[:100]}`                                    |
| `timeout`     | `⚠ Task timed out`                                       |
| `done`        | `None` (control signal, not displayed)                   |

### Change 4: Update `_queue_task()` — no changes needed

`_queue_task()` already calls `_poll_result()` with the right args. No changes.

---

## What the User Sees

**Before:**
```
Queued task abc12345...
Polling for result...
[silence for 60 seconds]
[wall of text]
```

**After:**
```
Working...
→ run_command: ssh root@R1 "cli -c 'show interfaces'"
  ✓ ge-0/0/0 UP, ge-0/0/1 UP
Configuring OSPF on R1...
→ run_command: ssh root@R2 "cli -c 'set protocols ospf...'"
  ✓ commit complete
[stream message deleted]
[clean final result as separate message]
```

---

## Edge Cases

1. **No stream events** (e.g. old Claude Code version without stream support): Falls back to polling `hcli:results:` — same as current behavior
2. **Stream arrives but no final result**: 30s polling window after stream ends; if no result, show timeout
3. **Telegram rate limit on edit_text()**: Catch exception, log, skip edit — stream continues
4. **Very long stream output**: Keep only last ~15 lines in display buffer to stay under 4096 char Telegram limit
5. **Concurrent tasks**: Each task has its own pub/sub channel (`hcli:stream:{task_id}`), no cross-talk

---

## Verification Steps

1. Deploy on h-srv, send "scan localhost with nmap"
2. Watch Telegram message update in real-time
3. Final result should replace the streaming message
4. Test timeout: send a long-running task, verify stream shows timeout
5. Test `--resume`: verify session continuity still works with new flags
6. Test chunking: verify 100KB chunking still triggers correctly
