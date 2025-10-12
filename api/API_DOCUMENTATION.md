# Arke IPFS Index API Documentation

## Overview

The Arke IPFS Index API is a FastAPI service that provides a high-level interface for querying and managing the IPFS-based entity storage system. It sits on top of the Kubo HTTP RPC API and provides cursor-based pagination, chain management, and snapshot coordination.

**Base URL**: `http://localhost:8000` (development) or configured production URL

**API Version**: 1.0.0

---

## Table of Contents

1. [Endpoints](#endpoints)
   - [Health Check](#get-health)
   - [List Entities](#get-entities)
   - [Get Index Pointer](#get-index-pointer)
   - [Append to Chain](#post-chainappend)
   - [Rebuild Snapshot](#post-snapshotrebuild)
2. [Data Models](#data-models)
3. [Architecture](#architecture)
4. [Error Handling](#error-handling)

---

## Endpoints

### GET /health

Health check endpoint to verify API service is running.

**Parameters**: None

**Response**: `200 OK`
```json
{
  "status": "healthy"
}
```

**Example**:
```bash
curl http://localhost:8000/health
```

---

### GET /entities

List entities with cursor-based pagination. Returns the most recent entities first, walking backwards through the chain.

**Parameters**:
- `limit` (query, optional, integer): Number of entities to return. Default: `10`
- `cursor` (query, optional, string): CID to continue pagination from. If not provided, starts from the most recent entity.

**Response**: `200 OK`
```json
{
  "items": [
    {
      "pi": "01K75GZSKKSP2K6TP05JBFNV09",
      "ver": 2,
      "tip": "bafyreicid...",
      "ts": "2025-10-09T14:30:00Z"
    }
  ],
  "total_count": 42,
  "has_more": true,
  "next_cursor": "baguqeeraid..."
}
```

**Response Fields**:
- `items`: Array of entity summaries
  - `pi`: Persistent identifier (ULID)
  - `ver`: Current version number
  - `tip`: CID of the latest manifest
  - `ts`: Timestamp when entity was created (from chain entry)
- `total_count`: Total number of entities across all snapshots and recent chain
- `has_more`: Boolean indicating if more entities exist
- `next_cursor`: CID to use for next page (null if no more entities)

**Pagination Behavior**:
1. First request (no cursor): Returns most recent entities from `recent_chain_head`
2. Subsequent requests: Use `next_cursor` from previous response
3. Walks chain backwards via `prev` links with O(limit) complexity
4. For each PI, fetches current tip from MFS to get latest version info

**Example**:
```bash
# Get first page (10 most recent entities)
curl "http://localhost:8000/entities?limit=10"

# Get next page
curl "http://localhost:8000/entities?limit=10&cursor=baguqeeraid..."
```

**Error Responses**:
- `500 Internal Server Error`: Chain traversal failed or IPFS communication error

---

### GET /index-pointer

Get the current index pointer, which tracks the state of the recent chain and snapshot system.

**Parameters**: None

**Response**: `200 OK`
```json
{
  "schema": "arke/index-pointer@v1",
  "latest_snapshot_cid": "baguqeeraid...",
  "snapshot_seq": 5,
  "snapshot_count": 200,
  "snapshot_ts": "2025-10-09T23:00:00Z",
  "recent_chain_head": "baguqeeraid...",
  "recent_count": 42,
  "total_count": 242,
  "last_updated": "2025-10-12T10:30:00Z"
}
```

**Response Fields**:
- `schema`: Schema version identifier
- `latest_snapshot_cid`: CID of the most recent snapshot (null if none)
- `snapshot_seq`: Sequence number of latest snapshot
- `snapshot_count`: Number of entities in latest snapshot
- `snapshot_ts`: Timestamp of latest snapshot (null if none)
- `recent_chain_head`: CID of most recent chain entry (never reset to null after first entity)
- `recent_count`: Number of new entities added since last snapshot
- `total_count`: Total entities = `snapshot_count` + `recent_count`
- `last_updated`: Timestamp of last index pointer update

**Use Cases**:
- Monitor recent chain growth
- Check if snapshot rebuild threshold is approaching
- Get current system state for debugging

**Example**:
```bash
curl http://localhost:8000/index-pointer
```

**Error Responses**:
- `500 Internal Server Error`: Failed to read index pointer from MFS

**Note**: If index pointer doesn't exist in MFS, returns initialized empty state with zero counts.

---

### POST /chain/append

Append a new persistent identifier (PI) to the recent chain. This endpoint should be called by the API wrapper after creating a new entity.

**Request Body**:
```json
{
  "pi": "01K75GZSKKSP2K6TP05JBFNV09"
}
```

**Request Fields**:
- `pi` (required, string): The persistent identifier (ULID) to append

**Response**: `200 OK`
```json
{
  "cid": "baguqeeraid...",
  "success": true
}
```

**Response Fields**:
- `cid`: The CID of the newly created chain entry
- `success`: Boolean indicating operation success

**Behavior**:
1. Reads current index pointer from MFS
2. Creates new `ChainEntry` with:
   - `pi`: The provided persistent identifier
   - `ts`: Current timestamp
   - `prev`: IPLD link to previous chain head (or null if first)
3. Stores chain entry as dag-json in IPFS (pinned)
4. Updates index pointer:
   - Sets `recent_chain_head` to new CID
   - Increments `recent_count`
   - Increments `total_count`
5. Checks if `recent_count >= REBUILD_THRESHOLD`
6. If threshold reached and `AUTO_SNAPSHOT=true`, triggers background snapshot build

**Auto-Snapshot Trigger**:
- Threshold configurable via `REBUILD_THRESHOLD` (default: 100)
- Spawns `/app/scripts/build-snapshot.sh` as background process
- Fire-and-forget: does not wait for completion
- Logs threshold warning and trigger status to stdout

**Example**:
```bash
curl -X POST http://localhost:8000/chain/append \
  -H "Content-Type: application/json" \
  -d '{"pi": "01K75GZSKKSP2K6TP05JBFNV09"}'
```

**Error Responses**:
- `500 Internal Server Error`: Failed to store chain entry or update index pointer
- `422 Unprocessable Entity`: Invalid request body (missing or malformed `pi`)

**Note**: The chain only stores PI + timestamp. Version and tip information is maintained separately in MFS at `/arke/index/{shard}/{PI}.tip`.

---

### POST /snapshot/rebuild

Manually trigger a snapshot rebuild operation.

**Parameters**: None

**Request Body**: None

**Response**: `200 OK`
```json
{
  "message": "Snapshot rebuild should be triggered via build-snapshot.sh script"
}
```

**Behavior**:
Currently returns a placeholder message directing users to use the shell script directly.

**Recommended Usage**:
Instead of using this endpoint, run the snapshot build script directly:
```bash
# Inside container
/app/scripts/build-snapshot.sh

# From host
docker exec ipfs-node /app/scripts/build-snapshot.sh
```

**Example**:
```bash
curl -X POST http://localhost:8000/snapshot/rebuild
```

**Note**: This endpoint is a placeholder for future implementation. The snapshot rebuild process is currently designed to be run via the shell script, which has access to both the Kubo CLI (for dag-json operations) and the HTTP API.

---

## Data Models

### IndexPointer

Tracks the current state of the entity index system.

```python
{
  "schema": "arke/index-pointer@v1",
  "latest_snapshot_cid": str | null,     # CID of latest snapshot
  "snapshot_seq": int,                   # Snapshot sequence number
  "snapshot_count": int,                 # Entities in snapshot
  "snapshot_ts": str | null,             # ISO 8601 timestamp
  "recent_chain_head": str | null,       # CID of most recent chain entry
  "recent_count": int,                   # New entities since snapshot
  "total_count": int,                    # Total entities
  "last_updated": str                    # ISO 8601 timestamp
}
```

Stored in MFS at: `/arke/index-pointer.json`

### ChainEntry

Represents a single entry in the recent chain.

```python
{
  "schema": "arke/chain-entry@v0",
  "pi": str,                             # Persistent identifier
  "ts": str,                             # ISO 8601 timestamp
  "prev": {"/":" str} | null             # IPLD link to previous entry
}
```

Stored as: dag-json DAG objects, linked via `prev` pointers

### EntitiesResponse

Response model for the `/entities` endpoint.

```python
{
  "items": [
    {
      "pi": str,                         # Persistent identifier
      "ver": int,                        # Current version number
      "tip": str,                        # CID of latest manifest
      "ts": str                          # Creation timestamp
    }
  ],
  "total_count": int,                    # Total entities in system
  "has_more": bool,                      # More entities available
  "next_cursor": str | null              # CID for next page
}
```

### AppendChainRequest

Request model for the `/chain/append` endpoint.

```python
{
  "pi": str                              # Persistent identifier to append
}
```

---

## Architecture

### System Components

```
┌─────────────────────────────────────────────────────────┐
│                    Arke API Wrapper                     │
│              (Higher-level application logic)            │
└────────────────────┬────────────────────────────────────┘
                     │
                     │ HTTP requests
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Arke IPFS Index API (FastAPI)              │
│                     Port 8000                           │
├─────────────────────────────────────────────────────────┤
│  Endpoints:                                             │
│  • GET  /entities        - Query entities with cursor   │
│  • GET  /index-pointer   - Get system state             │
│  • POST /chain/append    - Add new entity to chain      │
│  • POST /snapshot/rebuild - Trigger snapshot            │
└────────────────────┬────────────────────────────────────┘
                     │
                     │ HTTP RPC calls
                     ▼
┌─────────────────────────────────────────────────────────┐
│                Kubo HTTP RPC API                        │
│                     Port 5001                           │
├─────────────────────────────────────────────────────────┤
│  Operations:                                            │
│  • /api/v0/dag/put       - Store DAG objects            │
│  • /api/v0/dag/get       - Retrieve DAG objects         │
│  • /api/v0/files/read    - Read from MFS                │
│  • /api/v0/files/write   - Write to MFS                 │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

#### Creating a New Entity
1. API Wrapper creates manifest and components via Kubo RPC
2. API Wrapper writes `.tip` file to MFS via Kubo RPC
3. API Wrapper calls `POST /chain/append` with PI
4. Index API creates chain entry and updates index pointer
5. If threshold reached, triggers automatic snapshot rebuild

#### Querying Entities
1. Client calls `GET /entities` with optional cursor
2. Index API reads index pointer to get `recent_chain_head`
3. Walks chain backwards from cursor/head via `prev` links
4. For each PI, reads current tip from MFS
5. Fetches manifest to get version number
6. Returns items with `next_cursor` for pagination

#### Snapshot System
1. Recent chain grows as entities are added
2. When `recent_count >= REBUILD_THRESHOLD`, rebuild triggered
3. `build-snapshot.sh` script:
   - Walks entire recent chain
   - Merges with previous snapshot
   - Creates new snapshot DAG (dag-json)
   - Updates index pointer with new snapshot CID
   - Resets `recent_count` to 0

### Configuration

Settings loaded from environment variables (`.env` file):

```bash
# API Configuration
IPFS_API_URL=http://localhost:5001/api/v0
INDEX_POINTER_PATH=/arke/index-pointer.json
CHUNK_SIZE=1000                  # Not currently used (chunking removed)
REBUILD_THRESHOLD=100            # Entities before auto-snapshot
AUTO_SNAPSHOT=true               # Enable automatic snapshot builds
```

---

## Error Handling

### Standard Error Response

All endpoints return standard FastAPI error responses:

```json
{
  "detail": "Error description message"
}
```

### Common HTTP Status Codes

- `200 OK`: Request successful
- `422 Unprocessable Entity`: Invalid request body or parameters
- `500 Internal Server Error`: IPFS communication failure or internal error

### Error Scenarios

#### Chain Traversal Failures
- **Cause**: Referenced CID not found, network timeout, corrupted chain entry
- **Response**: `500 Internal Server Error`
- **Mitigation**: Ensure all chain entries are pinned; check IPFS node health

#### MFS Read/Write Failures
- **Cause**: File not found, permission issues, IPFS node down
- **Response**: `500 Internal Server Error`
- **Mitigation**: Verify MFS paths exist; check Kubo node status

#### Pagination Edge Cases
- **Empty chain**: Returns empty items array with `has_more: false`
- **Invalid cursor**: May return 500 if CID doesn't exist or isn't a valid chain entry
- **Deleted tip file**: Query will fail with 500; tip files should never be deleted

### Debugging

Enable detailed logging by setting environment variables:
```bash
PYTHONUNBUFFERED=1  # Real-time logging output
```

Check logs:
```bash
# Docker Compose
docker compose logs -f ipfs-api

# Direct container
docker logs -f ipfs-node
```

---

## Examples

### Complete Pagination Flow

```bash
# Get first page
curl "http://localhost:8000/entities?limit=5"
# Response includes next_cursor: "baguqeera..."

# Get second page
curl "http://localhost:8000/entities?limit=5&cursor=baguqeera..."
# Response includes next_cursor: "baguqeerb..."

# Continue until has_more: false
curl "http://localhost:8000/entities?limit=5&cursor=baguqeerb..."
```

### Monitor System State

```bash
# Check current state
curl http://localhost:8000/index-pointer | jq .

# Watch recent count grow
watch -n 5 'curl -s http://localhost:8000/index-pointer | jq .recent_count'

# Check total entities
curl http://localhost:8000/index-pointer | jq .total_count
```

### Create New Entity (Full Flow)

```bash
# 1. Generate ULID for PI
PI=$(date +%s | md5 | head -c 26 | tr '[:lower:]' '[:upper:]')

# 2. Create manifest and store via Kubo RPC (API wrapper responsibility)
# ... (see API_WALKTHROUGH.md for details)

# 3. Notify index API
curl -X POST http://localhost:8000/chain/append \
  -H "Content-Type: application/json" \
  -d "{\"pi\": \"$PI\"}"

# 4. Verify entity appears in list
curl "http://localhost:8000/entities?limit=1" | jq .
```

---

## Performance Considerations

### Query Performance
- **O(limit)**: Walking chain scales linearly with page size
- **Tip reads**: Each entity requires 2 additional IPFS calls (tip + manifest)
- **Optimization**: Use larger `limit` values to reduce round trips for bulk queries

### Chain Growth
- Recent chain grows unbounded until snapshot
- Configure `REBUILD_THRESHOLD` based on acceptable query latency
- Lower threshold = more frequent snapshots = shorter chains = faster queries
- Higher threshold = fewer snapshots = longer chains = slower queries

### Snapshot Impact
- Snapshot builds can take several seconds for large chains
- Auto-snapshot runs in background without blocking API
- Consider manual snapshots during maintenance windows for large systems

### Recommended Settings
- Development: `REBUILD_THRESHOLD=10`, `AUTO_SNAPSHOT=true`
- Production: `REBUILD_THRESHOLD=100-1000`, `AUTO_SNAPSHOT=true`
- Low-traffic: `REBUILD_THRESHOLD=500+`, `AUTO_SNAPSHOT=false` (manual snapshots)

---

## Security

### CORS Configuration
Currently configured with permissive CORS:
```python
allow_origins=["*"]
allow_credentials=True
allow_methods=["*"]
allow_headers=["*"]
```

**Production**: Restrict `allow_origins` to specific domains.

### Port Binding
- Development: All ports exposed
- Production: Bind 8000 to `127.0.0.1` (localhost only)
- Access via reverse proxy (nginx, traefik, etc.)

### Input Validation
- All endpoints use Pydantic models for request validation
- ULID format should be validated before calling `/chain/append`
- No authentication/authorization implemented (add via middleware if needed)

---

## Related Documentation

- `API_WALKTHROUGH.md`: Complete guide to implementing entity operations via Kubo RPC
- `DISASTER_RECOVERY.md`: Snapshot system and backup procedures
- `CLAUDE.md`: Project overview and architecture
- `README.md`: Deployment and setup instructions

---

## Version History

- **1.0.0** (2025-10-12): Initial API implementation
  - Entity listing with cursor pagination
  - Chain append with auto-snapshot trigger
  - Index pointer management
  - Snapshot rebuild placeholder
