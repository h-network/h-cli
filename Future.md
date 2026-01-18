# h-cli Future: AI Memory Architecture

## Vision

A daily cycle where the bot interacts all day, accumulates raw conversation data, and an external nightly process builds a vector DB that gives the bot long-term memory the next day.

## Architecture

```
        ═══════════════ DAY (h-srv, lightweight) ═══════════════

  Telegram
     │
     ▼
┌──────────┐   ┌───────┐   ┌──────────────┐   ┌──────────┐
│ telegram  │──►│ Redis │──►│ claude-code  │──►│   core   │
│   bot     │◄──│       │◄──│ (dispatcher) │   │  (MCP)   │
└──────────┘   └───┬───┘   └──────┬───────┘   └──────────┘
                   │              │
                   │              │ Reads at query time
                   │              ▼
                   │    ┌───────────────────┐
                   │    │  Vector DB (ro)   │  Mounted volume
                   │    │  /data/memory/    │  Built last night
                   │    │                   │  Swappable backend
                   │    │  "last week you   │
                   │    │   scanned .1.1,   │
                   │    │   found 4 ports"  │
                   │    └───────────────────┘
                   │
                   │  Writes (raw JSON, no processing)
                   ▼
         ┌───────────────────┐
         │  hcli:memory:*    │  Plain JSON accumulates all day
         │  tagged: chat_id  │
         └───────────────────┘
         ┌───────────────────┐
         │  hcli:session:*   │  Claude CLI --resume (4h TTL)
         └───────────────────┘


        ═══════════════ NIGHT (h-oracle/h-titan) ═══════════════

  ┌─────────────────────────────────────────────────┐
  │  Nightly batch job (GPU machine)                │
  │                                                 │
  │  1. Pull hcli:memory:* from Redis (by chat_id)  │
  │  2. Chunk and clean conversations               │
  │  3. Generate embeddings (GPU model)             │
  │  4. Build/update vector DB                      │
  │  5. Copy to h-srv → /data/memory/               │
  │  6. Flush processed hcli:memory:* keys          │
  └─────────────────────────────────────────────────┘

        ═══════════════ NEXT MORNING ═══════════════

  Bot restarts → loads fresh vector DB
  Now remembers everything from yesterday + all history
```

## Two Layers of Memory

### Layer 1: Session Context (short-term, runtime)
- Claude CLI `--resume --session-id` per chat
- Full conversation replay within a session (4h TTL)
- Zero overhead — Claude CLI handles it natively
- Resets on session expiry or `/new` command

### Layer 2: Vector Memory (long-term, built offline)
- Raw data stored during the day as JSON in Redis
- Nightly process on GPU machine builds vector DB
- Mounted read-only in dispatcher container next day
- Queried before each Claude call to inject relevant past context

## Swappable Vector DB Backend

The dispatcher talks to a `MemoryBackend` interface:

```python
class MemoryBackend:
    def search(self, query: str, chat_id: str, k: int = 5) -> list[dict]:
        """Return k most relevant past interactions for this chat."""
        ...

    def is_available(self) -> bool:
        """Check if memory DB is loaded and ready."""
        ...
```

Implementations:
- `NullBackend` — no memory, just sessions (default on fresh install)
- `ChromaBackend` — ChromaDB SQLite file (zero server, mounted volume)
- `RedisStackBackend` — Redis Stack with RediSearch vectors
- `QdrantBackend` — Qdrant (if running separately)

Config via env var: `MEMORY_BACKEND=none|chroma|redis|qdrant`

## Query-time Embedding Problem

Vector search requires embedding the query at search time. Options:

| Approach | Pros | Cons |
|----------|------|------|
| Tiny ONNX model (fastembed, ~30MB) | Self-contained, fast on CPU | Still a model on the server |
| Daily summary file | Zero ML at runtime, text injection | Less precise, no semantic search |
| Remote embedding API (Ollama on h-oracle) | No local model | Dependency on h-oracle being online |

Recommended: Start with daily summary (zero ML), upgrade to fastembed if semantic search is needed.

### Daily Summary Approach (simplest)
The nightly job also generates a plain text summary per chat:
```
/data/memory/chat_9912_summary.txt:
"Recent activity: Scanned 192.168.1.1 (4 open ports: 22,80,443,8080).
Ran MTR to 8.8.8.8 (10 hops, 8.5ms avg). Checked DNS for example.com."
```
Dispatcher reads this file and injects via `--append-system-prompt`. No embedding needed at runtime.

## Nightly Batch Job (runs on h-oracle or h-titan)

```bash
#!/bin/bash
# /opt/h-cli-memory/nightly.sh

# 1. Export today's conversations from Redis
redis-cli -h h-srv -p 6379 --scan --pattern "hcli:memory:*" | \
  xargs -I {} redis-cli -h h-srv GET {} > /tmp/daily_export.jsonl

# 2. Generate embeddings + build vector DB
python3 build_memory.py \
  --input /tmp/daily_export.jsonl \
  --db /tmp/memory_db/ \
  --model nomic-embed-text \
  --ollama-url http://localhost:11434

# 3. Generate daily summaries per chat
python3 build_summaries.py \
  --input /tmp/daily_export.jsonl \
  --output /tmp/memory_db/summaries/

# 4. Copy to h-srv
rsync -az /tmp/memory_db/ h-srv:/opt/h-cli-data/memory/

# 5. Flush processed keys from Redis
redis-cli -h h-srv EVAL "for _,k in ipairs(redis.call('KEYS','hcli:memory:*')) do redis.call('DEL',k) end" 0

# 6. Restart dispatcher to pick up new DB
ssh h-srv "docker restart h-cli-claude"
```

## Data Format (hcli:memory:* in Redis)

```json
{
  "chat_id": "9912",
  "role": "user",
  "content": "scan 192.168.1.1 with nmap",
  "timestamp": 1707500000.0,
  "task_id": "abc-123"
}
```

Every message exchange produces two keys:
- `hcli:memory:<task_id>:user` — what the user said
- `hcli:memory:<task_id>:asst` — what Claude responded

No TTL — data accumulates until the nightly job flushes it.

## Implementation Phases

### Phase 1: Sessions + Raw Storage — DONE
- `--resume`/`--session-id` per chat_id
- Store conversations as JSON in Redis (`hcli:memory:*`)
- `/new` command to reset session
- `SESSION_TTL` env var (default 4h)
- Retry logic: falls back to fresh session if `--resume` fails

### Phase 2: Daily Summaries (implement later)
- Nightly job generates text summaries per chat
- Dispatcher loads summaries from mounted volume
- Injects via `--append-system-prompt`
- Zero ML on server

### Phase 3: Vector Search (implement when needed)
- Nightly job builds vector DB (ChromaDB/Qdrant)
- Dispatcher loads DB from volume
- Needs tiny embedding model for query-time (fastembed)
- Or remote API to h-oracle Ollama
- Swappable backend interface

## Volumes

```yaml
# docker-compose.yml additions (future)
volumes:
  memory-db:        # Vector DB / summaries, rebuilt nightly
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /opt/h-cli-data/memory
```
