# h-cli Roadmap

## Current State (February 2026)

| Feature | Status | Notes |
|---|---|---|
| Token efficiency | DONE | 85% total savings (plain text + brevity) |
| Session pruning | DONE | Not needed -- plain text injection eliminates bloat |
| Selective context loading | DONE | Skill/context files matched per-message |
| AsimovFirewall | DONE | 4-layer: pattern denylist + Haiku gate |
| Multi-user sessions | DONE | Redis keys per chat_id, ready to enable |
| Container isolation | DONE | Per-container networks, no lateral movement |
| Modular prompt stack | DONE | groundRules -> CLAUDE.md -> context.md |
| Platform support | DONE | Docker everywhere (ARM, x86, any cloud) |
| Olivaw Learning | DONE | Skill files, selective loading, interactive teaching, action system |
| Branch cleanup | DONE | Single main branch, Gitea + GitHub remotes |

## Planned

### CVE Auto-Population
- Weekly interactive `cve-update` command
- Fresh Claude instance (clean room, no h-cli context)
- Reviews NVD/GitHub Advisory for tools in sudo whitelist
- Proposes patterns, user approves, writes to blocked_patterns.txt
- Priority: HIGH (before next public release)

### Model Provider Routing
- Ollama / vLLM support for local GPU inference
- `MODEL_PROVIDER` env var: claude, ollama, vllm, auto
- Auto mode: route sensitive queries to local, complex to cloud
- Context layer is provider-agnostic (plain text in, plain text out)
- Swap one function in dispatcher, everything else untouched
- Priority: LOW (waiting on GPU hardware)

### Additional Channels
- Discord adapter (discord.py, same Redis pattern)
- Slack adapter (slack_bolt, webhook mode)
- WebChat (FastAPI + WebSocket)
- Signal bridge (signal-cli)
- Matrix (matrix-nio, self-hosted friendly)
- Architecture ready: each channel = independent container on Redis bus
- Priority: LOW

### Lambda Training Pipeline
- Orchestrate GPU training on Lambda Labs from Telegram
- Skill-only integration — no code changes, just API key + SSH key
- Pipeline: PDF > Docling > Classifier > Verifier > QA Gen > QA Verifier > Fine-Tuner > Benchmark
- Lambda Cloud API for instance lifecycle, SSH for deployment
- Priority: LOW

## Architecture Comparison

How h-cli compares against [OpenClaw](https://github.com/openclaw/openclaw):

```
Feature                   OpenClaw   h-cli
------------------------------------------------------------
Token efficiency          JSONL replay            Plain text injection
                          + pruning engine         (nothing to prune)
                          ~10 config knobs         3 config knobs

Session management        Gateway-owned            Redis per chat_id
                          WebSocket sessions       Stateless per-request
                          Event replay on reconnect No replay needed

Context pruning           Soft-trim + hard-clear   Not needed
                          Tool result eviction     Tool calls never enter
                          Cache TTL management     context in first place

Skill/tool definition     Static config files      Per-message selective load
(Olivaw Learning)         All tools always loaded   User teaches bot via Telegram
                          No learning capability    Bot writes skill files + git commits

Security                  Config-based isolation   AsimovFirewall (4-layer)
(AsimovFirewall)          Auth tokens in browser   Pattern denylist + LLM gate
                          Gateway token env var    HMAC signing + container isolation

Architecture              Star topology            Bus topology (Redis)
                          Gateway = single point   Components restart independently
                          of failure               No shared process

Multi-user                Device pairing           Chat ID keying (built in)
                          Challenge-nonce auth     Already separated in Redis
                          Scope config policies    Sessions, history, chunks per user

Channels                  20+ built-in             1 (Telegram) + arch ready
                          Monolithic gateway       Container-per-channel
                          Shared plugin deps       Independent adapters on Redis

Model providers           20+ providers            1 (Claude) + easy to add
                          Auth profile rotation    Provider-agnostic context layer
                          Failover chains          Swap one function

Platform support          Per-OS install paths     docker compose up -d
                          Companion apps           Same command everywhere
                          LaunchAgent / systemd    Docker handles supervision

Security posture          Patched by upstream dev   Operator-controlled
                          Wait for disclosure       CVE auto-population (planned)
                          135k exposed instances    Pattern denylist updated weekly
                          Critical RCE (2026)       No web UI, no exposed ports
```
