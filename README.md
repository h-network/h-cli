# h-cli

Natural language infrastructure management via Telegram.

Send a message. Get it done.

## See it in action

### Build a Juniper lab from scratch

<!-- VIDEO: Create 3 vRouters, wire ring topology, boot -->

> "Create 3 Juniper routers, connect them as a ring in EVE-NG and boot them up"

### Configure the network

<!-- VIDEO: Configure IPs + OSPF area 0 on the ring -->

> "Configure /30 IPs from 10.0.0.0/24 on the p2p links and configure OSPF area 0"

### Verify the result

<!-- VIDEO: OSPF summary report -->

> "Give me a summary of the OSPF network in the topology you just created"

### More examples

![Deploy customer lab](docs/gifs/deploy-lab.gif)

> "Deploy customer Acme from NetBox in EVE-NG" — pulls the topology, creates nodes, wires interfaces, lab is live.

![Network scan](docs/gifs/network-scan.gif)

> "Scan the network and report MAC address vendors" — runs the scan, resolves OUIs, returns a formatted report.

---

## What it is

A Telegram bot backed by Claude Code. You type plain English, it executes commands in a hardened container and returns results. Session context persists for 4 hours — it remembers "that host" and "same scan again."

```
"scan 192.168.1.1"              →  nmap results in 10 seconds
"check port 443 on that host"   →  remembers which host you meant
"deploy customer X in EVE-NG"   →  pulls from NetBox, builds the lab
```

Runs on your Claude Max/Pro subscription. Zero API costs.

## How it fits your infrastructure

h-cli is the AI interface, not the security boundary. It's one half of a complete solution:

```
┌─────────────────────────────────────────────────────────────────────┐
│  h-cli (application layer)                                         │
│                                                                     │
│  Conversational interface + Asimov firewall + pattern denylist      │
│  Prevents the LLM from generating dangerous commands                │
│  Defense-in-depth — catches mistakes before they reach your infra   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Your infrastructure (trust boundary)                               │
│                                                                     │
│  Read-only TACACS/RADIUS users — can show, can't configure          │
│  Scoped API tokens — read-only NetBox, viewer-role Grafana          │
│  SSH keys with forced commands or restricted shells                  │
│  Firewall rules — h-cli's source IP can only reach allowed targets  │
└─────────────────────────────────────────────────────────────────────┘
```

**h-cli doesn't ask you to trust it. It works within the trust you've already built.**

Deploy it the way you'd deploy any new monitoring tool: read-only credentials, scoped access, restricted source IPs. h-cli adds intelligence on top, not risk.

## Security

Four containers, two isolated Docker networks, 44 hardening items implemented.

- **Asimov firewall** — MCP proxy with two layers: pattern denylist (deterministic, zero latency) + independent Haiku gate (semantic analysis, resistant to prompt injection)
- **Network isolation** — frontend and backend on separate Docker networks; only the dispatcher bridges both
- **Non-root, least privilege** — all containers run as uid 1000, `cap_drop: ALL`, `no-new-privileges`, read-only rootfs on telegram-bot
- **HMAC-signed results** — prevents Redis result spoofing between containers

Full details: [Security](docs/security.md) · [Hardening audit trail](SECURITY-HARDENING.md)

## Quick Start

```bash
./install.sh                                       # creates .env + context.md, generates SSH keypair, builds
nano .env                                          # set TELEGRAM_BOT_TOKEN, ALLOWED_CHATS
nano context.md                                    # describe what YOUR deployment is for
ssh-copy-id -i ssh-keys/id_ed25519.pub user@host   # add the generated key to your servers
docker compose run -it --entrypoint bash claude-code  # one-time: shell in, run 'claude' to login
docker compose up -d
```

## Usage

**Natural language** (any plain text message):
```
scan localhost with nmap
ping 8.8.8.8
trace the route to google.com
check open ports on 192.168.1.1
deploy customer Acme from NetBox in EVE-NG
```

**Commands**:
```
/run nmap -sV 10.0.0.1    — execute a shell command directly
/new                       — clear context, start a fresh conversation
/status                    — show task queue depth
/help                      — available commands
```

## log4AI — Shell Command Logger

Drop-in shell logger that captures every command + output as structured JSONL. Bash and zsh. No dependencies.

```bash
cd log4ai && ./install.sh
```

```json
{"timestamp":"2026-02-10T14:30:00Z","host":"srv-01","command":"nmap -sV 192.168.1.1","exit_code":0,"duration_ms":12400}
```

Sensitive commands (passwords, tokens, keys) are automatically blacklisted.

---

## Docs

- [Architecture](docs/architecture.md) — containers, networks, data flow
- [Security](docs/security.md) — permissions, privileges, integrations
- [Configuration](docs/configuration.md) — environment variables, authentication
- [Test Cases](docs/test-cases/) — real-world security boundary testing

## Contact

h-cli is part of a larger ecosystem. Interested?

Reach out: **[halil@hb-l.nl](mailto:halil@hb-l.nl)**

---

*Built for engineers who want their tools to learn.*
