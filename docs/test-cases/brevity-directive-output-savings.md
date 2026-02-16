# Test Case: Brevity Directive — Output Token Savings

## Objective

Measure the output token and cost difference when a CLAUDE.md brevity
directive is present vs default Claude behavior.

## Background

h-cli dispatches user messages to Claude Code via `claude -p`. Without
output constraints, Claude produces verbose, essay-style responses with
emoji, bullet-point breakdowns, and unsolicited follow-up suggestions —
even for simple questions like "what time is it?".

Two lines added to `claude-code/CLAUDE.md` enforce conciseness:

```
- **Be brutally concise.** One sentence if possible. No apologies, no emoji,
  no self-reflection, no bullet-point breakdowns of what you did wrong.
  Answer the question, report the result, stop. Your reply MUST fit in a
  single Telegram message. Only add detail when the user explicitly asks
  for more.
- **Plain markdown only.** Never output HTML tags. Use **bold**, *italic*,
  `code` — never `<b>`, `<i>`, `<code>`. The bot converts markdown to
  Telegram HTML; raw HTML breaks it.
```

## Test Design

Same question, same model, same machine. Two directories — one with a
CLAUDE.md containing the brevity rules, one empty (default behavior).

**Model**: `claude-sonnet-4-5-20250929`
**Claude Code version**: 2.1.42

**Test message**: `"explain the OSI model and how a packet travels from source to destination"`

**Method A (default)**: `cd /tmp/test-default && claude -p --output-format stream-json --verbose --model sonnet -- "<message>"`

**Method B (concise)**: `cd /tmp/test-concise && claude -p --output-format stream-json --verbose --model sonnet -- "<message>"`
(CLAUDE.md with brevity directive present in working directory)

## Results

```
============================================================
  A: Default (no CLAUDE.md)
============================================================
  Input tokens:        3
  Cache read:          17,837
  Cache create:        1,595
  TOTAL INPUT:         19,435
  Output tokens:       1,106
  Cost:                $0.0466
  API duration:        23,122ms

============================================================
  B: Concise (CLAUDE.md brevity directive)
============================================================
  Input tokens:        3
  Cache read:          17,837
  Cache create:        1,735
  TOTAL INPUT:         19,575
  Output tokens:       183
  Cost:                $0.0244
  API duration:        5,353ms
```

### Summary

| Metric | Default (A) | Concise (B) | Savings |
|--------|-------------|-------------|---------|
| Output tokens | 1,106 | 183 | **83% fewer** |
| Input tokens | 19,435 | 19,575 | +140 (CLAUDE.md overhead) |
| Cost (per call) | $0.0466 | $0.0244 | **48% cheaper** |
| API duration | 23,122ms | 5,353ms | **77% faster** |

## Analysis

- **Token reduction**: 83% fewer output tokens. The brevity directive
  costs ~140 extra input tokens but saves 923 output tokens per call —
  a 6.6:1 return ratio.

- **Cost reduction**: 48% cheaper per invocation. Output tokens are priced
  higher than input tokens, so reducing output has outsized cost impact.

- **Speed**: 77% faster wall-clock time. Fewer output tokens = less
  generation time. The default response took 23s; the concise response
  took 5s.

- **Quality**: The concise response covered the same material (7 layers,
  encapsulation, de-encapsulation, routing) in 183 tokens. The default
  response added worked examples, return journey details, and PDU
  nomenclature — useful for a tutorial, excessive for an ops assistant.

## Real-World Comparison (Prod vs Dev)

Deployed side-by-side on h-srv, same connectivity analysis prompt:

**Prod (no brevity directive)**: ~100 lines, emoji, tables, MTU discovery,
ISP analysis, "Want me to..." follow-up suggestions.

**Dev (with brevity directive)**: ~15 lines. Ping, path, analysis, status. Done.

## Combined Savings with Plain Text Injection

When stacked with the plain text context injection (71% input savings,
see [resume-vs-plaintext-context.md](resume-vs-plaintext-context.md)):

| Optimization | Saves | Applies to |
|---|---|---|
| Plain text injection | 71% | Input tokens |
| Brevity directive | 83% | Output tokens |
| **Combined** | **~85%** | **Total tokens per interaction** |

The two optimizations are complementary — one reduces input, the other
reduces output. Together they cut total token usage by roughly 85%
compared to the original `--resume` + default verbose output.

## Implementation

- `claude-code/CLAUDE.md`: Two-line directive enforcing conciseness and
  plain markdown output
- `groundRules.md` Layer 4 B2: "Concise answers. Report results, not
  essays." (softer reinforcement)
- No code changes required — purely a prompt engineering optimization
