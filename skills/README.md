# h-cli Skills

Skill files teach Olivaw domain-specific knowledge. They are selectively
loaded into the system prompt when the user's message matches their keywords.

## File Format

Each skill is a Markdown file with a YAML-style keywords header:

```markdown
---
keywords: ospf, routing, area, cost, adjacency, dead-interval
---
# OSPF

## Juniper Configuration
- `set protocols ospf area 0 interface ge-0/0/1`
- Cost defaults to reference-bandwidth / interface-bandwidth

## Troubleshooting
- `show ospf neighbor` -- check adjacency state
- `show ospf database` -- verify LSA flooding
```

## Rules

- **keywords** (comma-separated): matched case-insensitively against the user's message.
  If any keyword appears as a word in the message, the skill is loaded.
- Files without a keywords header are only matched by filename (e.g. `ospf.md`
  matches when "ospf" appears in the message).
- This file (README.md) has no keywords header and no matching filename,
  so it is never injected into prompts.
- Total injected skill content is capped at 20KB per message.

## Adding Skills

1. Create `skills/your-topic.md` with the keywords header
2. Rebuild/restart the claude-code container (or just restart -- the volume
   mount picks up changes without a rebuild)
3. Test by sending a message containing one of the keywords

## Bot-Drafted Skills

The bot can write skill drafts to `/tmp/skills/` on the core container via
`run_command` (gated by the Asimov firewall). To review and approve:

```bash
docker cp h-cli-core:/tmp/skills/draft.md /tmp/
cat /tmp/draft.md        # review
cp /tmp/draft.md skills/  # approve
```

Execution from `/tmp/skills/` is blocked by the pattern denylist.
