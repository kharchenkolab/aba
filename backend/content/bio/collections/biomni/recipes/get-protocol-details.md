---
name: get-protocol-details
description: Fetch full details of a specific protocol from protocols.io by its numeric ID.
when_to_use: When the user has a protocols.io protocol ID (from a prior search) and wants the complete protocol record including steps, materials, and metadata.
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [protocols, protocols.io, protocol details, experimental methods, lab protocol, fetch by ID]
produces: [dict of full protocol fields as returned by protocols.io v3 API]
domain: protocols
source: biomni:tool/protocols.py::get_protocol_details
---
# Get Protocol Details

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Require a valid `PROTOCOLS_IO_ACCESS_TOKEN` environment variable.
2. GET `https://www.protocols.io/api/v3/protocols/{protocol_id}` with `Authorization: Bearer <token>`, timeout configurable (default 30 s).
3. Check `data["status_code"] == 0`; if not, return `{"error": ..., "status_code": ...}`.
4. Return `data["protocol"]` — the full protocol object with all fields provided by the API.

## Key decisions
- Returns the raw API protocol dict unchanged; downstream code can extract steps, reagents, or materials as needed.
- Timeout is a parameter to handle slow API responses.

## Caveats
- Requires the numeric protocol ID; obtain it from `search_protocols` first.
- Token must be valid and authorised; private protocols require appropriate permissions.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`; expose token via project env. Original impl: `source` -> lift to lakeFS later.
