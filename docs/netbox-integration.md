# NetBox Integration Notes

Operational knowledge from automating NetBox device management with h-cli. Covers device creation workflows, cable management gotchas, and critical verification patterns.

---

## The Golden Rule: Always Verify

**HTTP 200 OK does not mean the operation succeeded.**

NetBox can return 200 and silently fail. Always check actual state after every write operation.

```bash
# Wrong — trusting the status code
curl -X POST ... "$NETBOX_URL/api/dcim/cables/"
echo "Done"  # Maybe. Maybe not.

# Right — verify actual state
curl -X POST ... "$NETBOX_URL/api/dcim/cables/"
CABLE_COUNT=$(curl -s -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/cables/" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])")
echo "Cables: $CABLE_COUNT"  # Now you know.
```

This applies to devices, interfaces, IPs, and especially cables.

---

## Device Structure: Juniper vMX Example

### Prerequisites (must exist before creating devices)

| Object | Endpoint | Example |
|--------|----------|---------|
| Manufacturer | `/api/dcim/manufacturers/` | Juniper (slug: `juniper`) |
| Device Type | `/api/dcim/device-types/` | vMX (slug: `vmx`) |
| Device Role | `/api/dcim/device-roles/` | Router (slug: `router`) |
| Site | `/api/dcim/sites/` | LAB (slug: `lab`) |

### Device Properties

```json
{
    "name": "R1",
    "device_type": {"slug": "vmx"},
    "role": {"slug": "router"},
    "site": {"slug": "lab"},
    "status": "active",
    "serial": "VM6975C09948"
}
```

### Interface Structure (per vMX)

| Interface | Type | Purpose |
|-----------|------|---------|
| fxp0 | 1000base-t | Management |
| ge-0/0/0 through ge-0/0/9 | 1000base-t | Data plane |
| lo0 | virtual | Loopback |

12 interfaces total. If a device-type template exists in NetBox, interfaces are auto-created. Otherwise, create them manually via `POST /api/dcim/interfaces/`.

---

## Device Lifecycle

### Create

```bash
# Create device
curl -s -X POST \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"name":"R1","device_type":{"slug":"vmx"},"role":{"slug":"router"},"site":{"slug":"lab"},"status":"active"}' \
    "$NETBOX_URL/api/dcim/devices/"
```

### Assign Primary IP

```bash
# Create IP address on an interface
curl -s -X POST \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"address":"10.0.0.1/30","assigned_object_type":"dcim.interface","assigned_object_id":INTERFACE_ID}' \
    "$NETBOX_URL/api/ipam/ip-addresses/"

# Set as primary IP on device
curl -s -X PATCH \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"primary_ip4":IP_ADDRESS_ID}' \
    "$NETBOX_URL/api/dcim/devices/DEVICE_ID/"
```

### Delete

```bash
curl -s -X DELETE \
    -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/devices/DEVICE_ID/"
```

Deletion cascades to interfaces and IP assignments, but **NOT cables** (see below).

---

## Cable Management (Here Be Dragons)

### Creating Cables

```bash
curl -s -X POST \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "a_terminations": [{"object_type": "dcim.interface", "object_id": INTERFACE_A_ID}],
        "b_terminations": [{"object_type": "dcim.interface", "object_id": INTERFACE_B_ID}],
        "status": "connected"
    }' \
    "$NETBOX_URL/api/dcim/cables/"
```

### The Orphaned Cable Problem

When you delete a device, NetBox clears the cable terminations but **does not delete the cable object itself**. This leaves orphaned cables in the database.

**Symptoms:** Cable count doesn't match expectations. Recreating devices and cables fails because "orphaned" cables occupy slots.

**Fix:** Always clean up cables explicitly after deleting devices.

```bash
# List all cables
curl -s -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/cables/" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for c in data['results']:
    print(f'Cable {c[\"id\"]}: {c[\"a_terminations\"]} <-> {c[\"b_terminations\"]}')"

# Delete orphaned cable
curl -s -X DELETE \
    -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/cables/CABLE_ID/"
```

### Cleanup Workflow (Correct Order)

1. Delete devices (cascades interfaces and IPs)
2. List remaining cables
3. Delete orphaned cables manually
4. Verify: device count = 0, cable count = 0

---

## Complete Automation Workflow

**"Add routers from EVE-NG to NetBox":**

1. Discover running routers in EVE-NG (console port discovery)
2. For each router, connect via telnet and collect:
   - `show chassis hardware` → serial number
   - `show version` → model, version
   - `show interfaces terse` → interface list and status
3. Ensure prerequisites exist in NetBox (manufacturer, device type, role, site)
4. Create devices with discovered serial numbers
5. Create interfaces (or let device-type template handle it)
6. If topology is known: create cables between interfaces
7. Assign management IPs if applicable
8. **Verify everything** — check device count, interface count, cable count

---

## API Patterns

### Lookup by Name

```bash
# Find device by name
curl -s -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/devices/?name=R1"

# Find interface by device and name
curl -s -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/interfaces/?device=R1&name=ge-0/0/0"
```

### Bulk Operations

NetBox supports bulk create/update/delete on most endpoints:

```bash
# Create multiple interfaces at once
curl -s -X POST \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    -d '[
        {"device":DEVICE_ID,"name":"ge-0/0/0","type":"1000base-t"},
        {"device":DEVICE_ID,"name":"ge-0/0/1","type":"1000base-t"}
    ]' \
    "$NETBOX_URL/api/dcim/interfaces/"
```

### Pagination

NetBox paginates results (default 50 per page). For complete lists:

```bash
# Get total count
curl -s -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/devices/" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])"

# Get all results (set high limit)
curl -s -H "Authorization: Token $TOKEN" \
    "$NETBOX_URL/api/dcim/devices/?limit=1000"
```
