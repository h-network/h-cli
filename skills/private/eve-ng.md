---
keywords: eve-ng, eveng, eve, lab, topology, router, node, qemu, unl_wrapper, console, telnet
---
# EVE-NG Automation

## Golden Rule: SSH > REST API

The REST API is slow and partially broken. SSH to the EVE-NG host is 6x faster and more reliable.

| Method | Speed | Reliability |
|--------|-------|-------------|
| REST API | ~4 min per lab | Broken endpoints, session timeouts |
| SSH + unl_wrapper | ~42s per lab | Solid |

Use REST API only for read-only queries. For all lab operations: SSH first.

## SSH Lab Management

```bash
# List all labs
ssh eve-host "ls -la /opt/unetlab/labs/*.unl"

# Read lab topology (XML)
ssh eve-host "cat /opt/unetlab/labs/{labname}.unl"

# Start all nodes
ssh eve-host "sudo /opt/unetlab/wrappers/unl_wrapper -a start -T 0 -F /opt/unetlab/labs/{labname}.unl"

# Stop all nodes
ssh eve-host "sudo /opt/unetlab/wrappers/unl_wrapper -a stop -T 0 -F /opt/unetlab/labs/{labname}.unl -m 1"

# Delete lab
ssh eve-host "sudo /opt/unetlab/wrappers/unl_wrapper -a delete -T 0 -F /opt/unetlab/labs/{labname}.unl"
```

## Dynamic Console Port Discovery

Ports are assigned dynamically and change every restart. Discover at runtime:

```bash
ssh eve-host "pgrep -a qemu_wrapper | grep '{labname}'"
```

Key flags: `-C <port>` = console telnet port, `-D <id>` = node ID, `-t <name>` = router name.

## Interface Mapping (Critical)

**fxp0 is management only. Never use it for data plane.**

| Interface ID (XML) | Juniper Interface | Purpose |
|---------------------|-------------------|---------|
| `id="0"` | fxp0 | Management (leave unconnected) |
| `id="1"` | ge-0/0/0 | Data plane |
| `id="2"` | ge-0/0/1 | Data plane |

## P2P Topology in XML

Each point-to-point link needs its own invisible bridge:

```xml
<network id="1" type="bridge" name="R1-R2" visibility="0" />
```

## REST API (read-only use only)

```
POST /api/auth/login              # Auth (session cookie, expires ~10min)
GET  /api/status                  # System status
GET  /api/list/templates/         # Available node types
GET  /api/labs/{lab}.unl/topology # Lab topology
```

Known broken: `POST /api/labs/{name}.unl` (creation returns 404/412).
Lab locking: Cannot modify via API if lab is open in web UI (error 60061).
