---
keywords: netbox, dcim, ipam, device, interface, cable, ip address, inventory
---
# NetBox Integration

## Golden Rule: Always Verify

HTTP 200 OK does not mean the operation succeeded. NetBox can return 200 and silently fail. Always check actual state after every write operation.

## API Patterns

Base URL and token are in `$NETBOX_URL` and `$NETBOX_API_TOKEN`.

```bash
# Lookup device by name
curl -s -H "Authorization: Token $NETBOX_API_TOKEN" "$NETBOX_URL/api/dcim/devices/?name=R1"

# Create device
curl -s -X POST -H "Authorization: Token $NETBOX_API_TOKEN" -H "Content-Type: application/json" \
    -d '{"name":"R1","device_type":{"slug":"vmx"},"role":{"slug":"router"},"site":{"slug":"lab"},"status":"active"}' \
    "$NETBOX_URL/api/dcim/devices/"

# Assign primary IP
curl -s -X POST -H "Authorization: Token $NETBOX_API_TOKEN" -H "Content-Type: application/json" \
    -d '{"address":"10.0.0.1/30","assigned_object_type":"dcim.interface","assigned_object_id":INTERFACE_ID}' \
    "$NETBOX_URL/api/ipam/ip-addresses/"

# Delete device (cascades interfaces/IPs but NOT cables)
curl -s -X DELETE -H "Authorization: Token $NETBOX_API_TOKEN" "$NETBOX_URL/api/dcim/devices/DEVICE_ID/"
```

## Cable Management

Creating cables:
```bash
curl -s -X POST -H "Authorization: Token $NETBOX_API_TOKEN" -H "Content-Type: application/json" \
    -d '{"a_terminations":[{"object_type":"dcim.interface","object_id":A_ID}],"b_terminations":[{"object_type":"dcim.interface","object_id":B_ID}],"status":"connected"}' \
    "$NETBOX_URL/api/dcim/cables/"
```

**Orphaned cable problem:** Deleting a device clears cable terminations but does NOT delete the cable object. Always clean up cables explicitly after deleting devices.

Cleanup order: 1) Delete devices 2) List remaining cables 3) Delete orphaned cables 4) Verify counts = 0.

## Prerequisites (must exist before creating devices)

| Object | Endpoint | Example |
|--------|----------|---------|
| Manufacturer | `/api/dcim/manufacturers/` | Juniper (slug: `juniper`) |
| Device Type | `/api/dcim/device-types/` | vMX (slug: `vmx`) |
| Device Role | `/api/dcim/device-roles/` | Router (slug: `router`) |
| Site | `/api/dcim/sites/` | LAB (slug: `lab`) |

## Pagination

Default 50 per page. Use `?limit=1000` for complete lists. Check `count` field for totals.
