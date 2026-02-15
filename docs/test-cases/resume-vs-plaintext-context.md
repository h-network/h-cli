# Test Case: --resume (JSONL replay) vs Plain Text Context Injection

## Objective

Measure the token cost difference between two methods of providing
conversation history to Claude Code in headless (`claude -p`) mode.

## Background

h-cli dispatches user messages to Claude Code via `claude -p`. Between
messages, conversation continuity is needed so the LLM remembers context.

Two approaches:

1. **--resume** (original): Claude Code replays the full JSONL session
   file on every invocation. Contains all messages, tool calls, tool
   results, file snapshots — the complete interaction history in
   structured format.

2. **Plain text injection** (new): The dispatcher reads recent
   conversation turns from Redis, formats them as markdown, and prepends
   them to the user's message. Each `claude -p` call starts fresh with
   a new `--session-id`.

## Test Design

Same conversation, same question, same container (`claude-code`).

**Data source**: A real 2-hour h-cli session (2026-02-15 23:04 → 2026-02-16 01:06 UTC), 73 user turns.

| Source | Format | Size |
|--------|--------|------|
| JSONL session (`bf6071a7...`) | Structured JSONL with tool calls, file snapshots | 1,299 KB, 781 lines |
| Session chunk (text dump) | Plain text, `[timestamp] ROLE: content` | 181 KB, capped at 50 KB |

**Test message**: `"what did we talk about earlier?"`

**Method A**: `claude -p --resume bf6071a7... -- "what did we talk about earlier?"`

**Method B**: `claude -p -- "## Recent conversation\n{chunk_text}\n---\n\nwhat did we talk about earlier?"`

## Results

```
============================================================
  A: --resume (JSONL replay, 1.3MB)
============================================================
  Input tokens:        3
  Cache read:          17,836
  Cache create:        112,473
  TOTAL INPUT:         130,312
  Output tokens:       135
  Cost:                $0.7153
  API duration:        6,719ms

============================================================
  B: Plain text chunk (new method)
============================================================
  Input tokens:        3
  Cache read:          17,836
  Cache create:        19,368
  TOTAL INPUT:         37,207
  Output tokens:       339
  Cost:                $0.1385
  API duration:        12,662ms
```

### Summary

| Metric | --resume (A) | Plain text (B) | Savings |
|--------|-------------|----------------|---------|
| Input tokens | 130,312 | 37,207 | **71% fewer** |
| Cost (per call) | $0.7153 | $0.1385 | **81% cheaper** |
| API duration | 6,719ms | 12,662ms | -88% (more output) |

## Analysis

- **Token reduction**: 71% fewer input tokens. The JSONL format includes
  tool call/result JSON, file snapshots, and queue operations that inflate
  token count without adding conversational value.

- **Cost reduction**: 81% cheaper per invocation. On a subscription plan
  this doesn't translate to dollars, but it means more headroom within
  the context window (200K tokens) before hitting limits.

- **Speed**: Method B was slower in wall-clock time, but produced 2.5x
  more output tokens (339 vs 135) — a more detailed answer. The input
  processing itself is cheaper due to fewer tokens.

- **Quality tradeoff**: Plain text loses tool call details and file
  contents from the session. For h-cli's use case (infrastructure
  commands via Telegram), the user-assistant dialogue is what matters
  for continuity — not the internal tool interactions.

## Conclusion

Plain text context injection uses 71% fewer tokens than `--resume` for
the same conversation. The dispatcher now:

1. Stores conversation turns in Redis as they happen
2. Formats them as markdown and prepends to each new message
3. Uses a fresh `--session-id` per invocation (JSONL still archived)

Session chunks on disk and JSONL files remain as audit trails, but
are no longer replayed into the context window.

## Implementation

- `dispatcher.py`: `_build_conversation_context()` reads Redis
  `session_history`, formats as `[HH:MM] **ROLE**: content`
- `--resume` removed, `--session-id` with fresh UUID per call
- `original_message` stored in history (not the context-prepended version)
- Session chunks injected via system prompt for older context (>24h)
