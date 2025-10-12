# IPFS Server Architecture

**Complete technical specification for the Arke IPFS storage layer**

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Data Structures](#data-structures)
3. [PI Chain System](#pi-chain-system)
4. [Snapshot System](#snapshot-system)
5. [How Chain and Snapshots Interrelate](#how-chain-and-snapshots-interrelate)
6. [Storage Locations](#storage-locations)
7. [API Endpoints](#api-endpoints)
8. [Practical Examples](#practical-examples)
9. [Performance Characteristics](#performance-characteristics)

---

## System Overview

The Arke IPFS Server implements a **hybrid chain + snapshot architecture** optimized for:
- Fast entity creation and updates
- Efficient paginated queries
- Disaster recovery via CAR exports
- Duplicative complete-state snapshots

```
┌──────────────────────────────────────────────────────────────┐
│                     STORAGE LAYERS                           │
├──────────────────────────────────────────────────────────────┤
│  1. Recent Chain (dag-json)                                  │
│     → Fast appends, recent queries                           │
│     → Linked list:  E → D → C → B → A → null                │
│                                                              │
│  2. Snapshot System (dag-json)                               │
│     → Complete state backup at a point in time               │
│     → Chunked linked list for scalability                    │
│     → Used for: DR, mirroring (NOT queries)                  │
│                                                              │
│  3. Index Pointer (MFS file)                                 │
│     → Single source of truth for system state                │
│     → Points to recent_chain_head and latest_snapshot_cid    │
└──────────────────────────────────────────────────────────────┘
```

### Key Architectural Decisions

1. **Cursor-Only Pagination**: Removed offset-based pagination for simplicity
2. **Duplicative Snapshots**: Each snapshot contains ALL entities (not incremental)
3. **Chain Never Reset**: `recent_chain_head` always points to latest entity (never null after snapshot)
4. **DR-Only Snapshots**: Snapshots used only for disaster recovery, not for queries
5. **Chunk Linked Lists**: Snapshot chunks form chains (not arrays) for consistency

---

## Data Structures

### 1. Index Pointer

**Location**: `/arke/index-pointer` in MFS
**Codec**: dag-json
**Purpose**: Single source of truth for current system state

```json
{
  "schema": "arke/index-pointer@v1",
  "latest_snapshot_cid": "baguqeerakxhfly...",
  "snapshot_seq": 2,
  "snapshot_count": 2300,
  "snapshot_ts": "2025-10-11T18:44:31Z",
  "recent_chain_head": "baguqeerayq...",
  "recent_count": 0,
  "total_count": 2300,
  "last_updated": "2025-10-11T18:44:31Z"
}
```

**Fields**:
- `schema`: Always `"arke/index-pointer@v1"`
- `latest_snapshot_cid`: CID of most recent snapshot (null if none)
- `snapshot_seq`: Monotonic snapshot sequence number
- `snapshot_count`: Number of entities in latest snapshot
- `snapshot_ts`: Timestamp of latest snapshot build
- `recent_chain_head`: **CID of latest entity (never reset to null)**
- `recent_count`: Number of new entities since last snapshot
- `total_count`: Total entities across system
- `last_updated`: Last index pointer update timestamp

**Important**: `recent_chain_head` is NEVER set to null after snapshotting. It always points to the latest entity to maintain chain continuity. Only `recent_count` resets to 0.

---

### 2. Chain Entry

**Codec**: dag-json
**Purpose**: Individual entry in the recent chain
**Pin Strategy**: Pinned when created, unpinned after snapshot (optional)

```json
{
  "schema": "arke/chain-entry@v1",
  "pi": "01K75GZSKKSP2K6TP05JBFNV09",
  "ver": 2,
  "tip": {"/": "baguqeerayq..."},
  "ts": "2025-10-11T12:30:15Z",
  "prev": {"/": "baguqeerayq..."}
}
```

**Fields**:
- `schema`: Always `"arke/chain-entry@v1"`
- `pi`: Persistent identifier (ULID)
- `ver`: Current version number of this entity
- `tip`: IPLD link to manifest CID
- `ts`: Timestamp of entity creation/update
- `prev`: IPLD link to previous chain entry (null if first)

---

### 3. Snapshot

**Codec**: dag-json
**Purpose**: Complete state snapshot at a point in time
**Structure**: Links to HEAD chunk of linked list

```json
{
  "schema": "arke/snapshot@v3",
  "seq": 2,
  "ts": "2025-10-11T18:44:31Z",
  "prev_snapshot": {"/": "baguqeeras3ungl..."},
  "total_count": 2300,
  "chunk_size": 10000,
  "entries_head": {"/": "baguqeerakt..."}
}
```

**Fields**:
- `schema`: Always `"arke/snapshot@v3"`
- `seq`: Monotonic sequence number
- `ts`: Timestamp of snapshot creation
- `prev_snapshot`: IPLD link to previous snapshot (null if first)
- `total_count`: Total entities in this snapshot
- `chunk_size`: Entities per chunk (typically 10,000)
- `entries_head`: IPLD link to HEAD (newest) chunk

**Version History**:
- `@v1`: Original with array of chunk links
- `@v2`: Chunked array structure
- **`@v3`: Current - uses `entries_head` instead of chunk array**

---

### 4. Snapshot Chunk

**Codec**: dag-json
**Purpose**: Contains batch of entity entries
**Structure**: Forms linked list via `prev` field

```json
{
  "schema": "arke/snapshot-chunk@v2",
  "chunk_index": 0,
  "entries": [
    {
      "pi": "01K75GZSKKSP2K6TP05JBFNV09",
      "ver": 2,
      "tip": {"/": "baguqeerayq..."},
      "ts": "2025-10-11T12:30:15Z"
    }
    // ... up to 10,000 entries
  ],
  "prev": {"/": "baguqeerakt..."}
}
```

**Fields**:
- `schema`: Always `"arke/snapshot-chunk@v2"`
- `chunk_index`: Position in sequence (0 = oldest chunk)
- `entries`: Array of up to `chunk_size` entity records
- `prev`: IPLD link to previous (older) chunk (null if oldest)

**Traversal**: HEAD chunk (newest) → prev → prev → ... → null (oldest)

---

### 5. Manifest (Entity Version)

**Codec**: dag-cbor
**Purpose**: Version-controlled entity data
**Location**: IPFS blockstore (referenced by tip CIDs)

```json
{
  "schema": "arke/manifest@v1",
  "pi": "01K75GZSKKSP2K6TP05JBFNV09",
  "ver": 2,
  "ts": "2025-10-11T10:00:00Z",
  "prev": {"/": "baguqee..."},
  "components": {
    "metadata": {"/": "bafybei..."},
    "image": {"/": "bafybei..."}
  },
  "children_pi": ["01GX...", "01GZ..."],
  "note": "Version description"
}
```

**Fields**:
- `schema`: Always `"arke/manifest@v1"`
- `pi`: Persistent identifier
- `ver`: Version number (increments)
- `ts`: Timestamp
- `prev`: IPLD link to previous manifest version (null if v1)
- `components`: Named CID references to content
- `children_pi`: Array of child entity PIs
- `note`: Optional version description

---

## PI Chain System

### What is the PI Chain?

The PI chain is a **linked list of all entity operations** (creates/updates) stored as dag-json objects in IPFS.

```
recent_chain_head → [Entity E] → [Entity D] → [Entity C] → [Entity B] → [Entity A] → null
                      (newest)                                              (oldest)
```

### Chain Operations

#### 1. Append to Chain

When a new entity is created or updated:

```python
# 1. Get current index pointer
pointer = await index_pointer.get_index_pointer()

# 2. Create new chain entry
entry = ChainEntry(
    pi="01K75ZZZ...",
    ver=1,
    tip={"/": "baguqee..."},
    ts=datetime.now(timezone.utc).isoformat(),
    prev={"/": pointer.recent_chain_head} if pointer.recent_chain_head else None
)

# 3. Store as DAG-JSON (pinned)
response = await ipfs_dag_put(entry, codec="dag-json", pin=True)
new_cid = response["Cid"]["/"]

# 4. Update index pointer
pointer.recent_chain_head = new_cid
pointer.recent_count += 1
pointer.total_count += 1
await index_pointer.update_index_pointer(pointer)
```

#### 2. Query Chain (Cursor Pagination)

Walk the chain backwards from recent_chain_head:

```python
async def query_chain(limit: int = 10, cursor: str = None):
    pointer = await get_index_pointer()

    # Start from cursor or head
    current_cid = cursor or pointer.recent_chain_head

    if not current_cid:
        return [], None

    items = []

    for _ in range(limit):
        # Fetch chain entry
        entry = await ipfs_dag_get(current_cid)

        # Add to results
        items.append({
            "pi": entry["pi"],
            "ver": entry["ver"],
            "tip": entry["tip"]["/"],
            "ts": entry["ts"]
        })

        # Move to previous
        if not entry.get("prev"):
            return items, None  # End of chain

        current_cid = entry["prev"]["/"]

    # More items available
    return items, current_cid  # next_cursor
```

### Chain Continuity

**Critical**: The chain is NEVER broken. Even after snapshot builds:

```
Before Snapshot:
recent_chain_head → C → B → A → null
recent_count: 3

After Snapshot:
recent_chain_head → C (SAME! NOT NULL!)
recent_count: 0 (reset)

After Adding D:
recent_chain_head → D → C → B → A → null
                    ↑new  ↑preserved continuity!
```

This ensures:
- No gaps in the chain
- New entities can always link to previous
- Walking the chain always works end-to-end

---

## Snapshot System

### What is a Snapshot?

A snapshot is a **complete state backup** at a point in time, containing ALL entities in the system (not incremental). Snapshots are used ONLY for:
- Disaster recovery (CAR exports)
- Mirroring/replication
- **NOT for queries** (queries use the chain)

### Snapshot Structure

Snapshots use a **linked list of chunks** for scalability:

```
Snapshot Object
  └─ entries_head → Chunk 2 (entries 20000-22999)
                      └─ prev → Chunk 1 (entries 10000-19999)
                                  └─ prev → Chunk 0 (entries 0-9999)
                                              └─ prev → null
```

### Snapshot Build Process

**When**: Triggered when `recent_count >= REBUILD_THRESHOLD` (default: 10,000)

**Steps**:

1. **Walk Recent Chain**:
```bash
# Collect all entries from chain
chain_entries=$(walk_chain "$recent_chain_head")
# Result: [E, D, C, B, A] (newest to oldest)
```

2. **Load Previous Snapshot** (if exists):
```bash
# Read old snapshot and all its chunks
snapshot_entries=$(get_snapshot_entries "$prev_snapshot_cid")
# Result: [older entities from last snapshot]
```

3. **Merge** (DUPLICATIVE!):
```bash
# Merge: chain (newest) + snapshot (oldest)
all_entries=$(merge "$chain_entries" "$snapshot_entries")
# Result: ALL entities, including duplicates from prev snapshot
```

4. **Create Chunk Linked List**:
```bash
# Split into chunks of 10K each
# Link from OLDEST to NEWEST
Chunk 0 (prev=null) → Chunk 1 (prev=Chunk0) → Chunk 2 (prev=Chunk1)
# Return HEAD (Chunk 2)
```

5. **Create Snapshot Object**:
```json
{
  "schema": "arke/snapshot@v3",
  "seq": 3,
  "ts": "2025-10-11T20:00:00Z",
  "prev_snapshot": {"/": "old_snapshot_cid"},
  "total_count": 25000,
  "chunk_size": 10000,
  "entries_head": {"/": "chunk_2_cid"}
}
```

6. **Update Index Pointer**:
```python
# IMPORTANT: Keep recent_chain_head pointing to latest entity!
pointer.latest_snapshot_cid = new_snapshot_cid
pointer.snapshot_seq += 1
pointer.snapshot_count = total_count
pointer.recent_chain_head = chain_head  # NOT NULL!
pointer.recent_count = 0  # Reset to 0
```

### Snapshot Traversal

To read all entities from a snapshot:

```bash
# 1. Get snapshot object
snapshot=$(ipfs dag get $snapshot_cid)

# 2. Get entries_head
head_cid=$(echo "$snapshot" | jq -r '.entries_head["/"]')

# 3. Walk chunks backwards
current=$head_cid
while [[ -n "$current" && "$current" != "null" ]]; do
    chunk=$(ipfs dag get $current)

    # Process entries
    echo "$chunk" | jq '.entries[]'

    # Move to previous chunk
    current=$(echo "$chunk" | jq -r '.prev["/"] // empty')
done
```

---

## How Chain and Snapshots Interrelate

### Lifecycle of an Entity

```
1. CREATE Entity A
   └─ Append to chain: recent_chain_head → A → null
   └─ recent_count: 1

2. CREATE Entity B
   └─ Append to chain: recent_chain_head → B → A → null
   └─ recent_count: 2

3. CREATE Entity C
   └─ Append to chain: recent_chain_head → C → B → A → null
   └─ recent_count: 3

4. SNAPSHOT (threshold=3 for this example)
   └─ Walk chain: collect [C, B, A]
   └─ No previous snapshot: snapshot_entries = []
   └─ Merge: [C, B, A] + [] = [C, B, A]
   └─ Create snapshot with 3 entities
   └─ Update pointer:
       recent_chain_head: C (NOT NULL!)
       recent_count: 0 (reset)
       latest_snapshot_cid: snapshot1
       snapshot_count: 3

5. CREATE Entity D
   └─ Append to chain: recent_chain_head → D → C → B → A → null
   └─ recent_count: 1

6. CREATE Entity E
   └─ Append to chain: recent_chain_head → E → D → C → B → A → null
   └─ recent_count: 2

7. SNAPSHOT (threshold=3 not reached, but manual trigger)
   └─ Walk chain: collect [E, D, C, B, A]
   └─ Load previous snapshot: [C, B, A]
   └─ Merge: [E, D, C, B, A] + [C, B, A]
       └─ NOTE: C, B, A are DUPLICATED!
       └─ Total in snapshot: 5 entities (E, D, C, B, A)
   └─ Update pointer:
       recent_chain_head: E (still pointing to latest!)
       recent_count: 0
       latest_snapshot_cid: snapshot2
       snapshot_count: 5
```

### Query Flow

**Query: Get latest 10 entities**

```
1. GET /entities?limit=10
   └─ Read index pointer
   └─ Start from recent_chain_head
   └─ Walk chain backwards
   └─ Return items + next_cursor
```

**NOT using snapshots for queries!** Queries walk the chain directly.

### DR Flow

**Export CAR for backup:**

```bash
# 1. Build snapshot
./scripts/build-snapshot.sh
# → Creates snapshot with ALL entities
# → Snapshot CID: baguqeerakxhfly...

# 2. Export to CAR
./scripts/export-car.sh
# → Follows IPLD links from snapshot
# → Includes: snapshot + chunks + manifests + components
# → Output: backups/arke-2-20251011.car

# 3. Restore on fresh node
./scripts/restore-from-car.sh backups/arke-2-20251011.car
# → Imports CAR
# → Rebuilds MFS .tip files from snapshot
# → System fully restored
```

### Duplication Strategy

Snapshots are **DUPLICATIVE** not incremental:

```
Snapshot 1: [A, B, C] (3 entities)
  └─ Storage: 3 entity records

Add D, E, F

Snapshot 2: [A, B, C, D, E, F] (6 entities)
  └─ Storage: 6 entity records (A, B, C duplicated!)

Total storage: 3 + 6 = 9 entity records (for 6 unique entities)
```

**Why duplicative?**
- Simpler DR: Single snapshot contains complete state
- Faster restore: Don't need to walk snapshot chain
- Trade-off: More storage, but IPFS deduplicates blocks

---

## Storage Locations

### IPFS Blockstore

**What's stored:**
- Chain entries (dag-json)
- Snapshots (dag-json)
- Snapshot chunks (dag-json)
- Manifests (dag-cbor)
- Components (raw bytes)

**Pin status:**
- Chain entries: Pinned on creation, optionally unpinned after snapshot
- Snapshots: Pinned forever (unless manually unpinned)
- Chunks: Pinned forever (linked from snapshot)
- Manifests: Pinned (for current versions)

### MFS (Mutable File System)

**What's stored:**
- Index pointer: `/arke/index-pointer`
- Tip files: `/arke/index/{first-2-chars}/{next-2-chars}/{PI}.tip`

**Example**:
```
/arke/
  └─ index-pointer (JSON file)
  └─ index/
      └─ 01/
          └─ K7/
              ├─ 01K75GZSKKSP2K6TP05JBFNV09.tip → baguqeerayq...
              └─ 01K75HQQXNTDG7BBP7PS9AWYAN.tip → baguqeerayq...
```

### Local Filesystem

**Snapshots directory:**
```
snapshots/
  ├─ snapshot-1.json   (metadata)
  ├─ snapshot-2.json   (metadata)
  └─ latest.json       (symlink to latest)
```

**Backups directory:**
```
backups/
  ├─ arke-1-20251011-183000.car
  ├─ arke-1-20251011-183000.json
  ├─ arke-2-20251011-184430.car
  └─ arke-2-20251011-184430.json
```

---

## API Endpoints

### FastAPI Backend (Port 3000)

#### GET /health
Health check

**Response:**
```json
{"status": "healthy"}
```

#### GET /index-pointer
Get current system state

**Response:**
```json
{
  "schema": "arke/index-pointer@v1",
  "latest_snapshot_cid": "baguqee...",
  "snapshot_seq": 2,
  "recent_chain_head": "baguqee...",
  "recent_count": 0,
  "total_count": 2300,
  ...
}
```

#### GET /entities
List entities with cursor pagination

**Query Params:**
- `limit` (default: 10) - Number of items to return
- `cursor` (optional) - CID to continue from

**Response:**
```json
{
  "items": [
    {
      "pi": "01K75...",
      "ver": 2,
      "tip": "baguqee...",
      "ts": "2025-10-11T12:00:00Z"
    }
  ],
  "total_count": 2300,
  "has_more": false,
  "next_cursor": null
}
```

#### POST /chain/append
Append new entity to recent chain

**Request Body:**
```json
{
  "pi": "01K75ZZZ...",
  "tip_cid": "baguqee...",
  "ver": 1
}
```

**Response:**
```json
{
  "cid": "baguqeerayq...",
  "success": true
}
```

#### POST /snapshot/rebuild
Manually trigger snapshot rebuild

**Response:**
```json
{
  "message": "Snapshot rebuild should be triggered via build-snapshot.sh script"
}
```

---

## Practical Examples

### Example 1: Create Entity and Query

```bash
# 1. Create entity (via API wrapper - details omitted)
# → Manifest created: baguqeerayq...
# → .tip file written

# 2. Append to chain
curl -X POST http://localhost:3000/chain/append \
  -H "Content-Type: application/json" \
  -d '{
    "pi": "01K75TEST001",
    "tip_cid": "baguqeerayq...",
    "ver": 1
  }'
# → {"cid": "baguqeera...", "success": true}

# 3. Query entities
curl http://localhost:3000/entities?limit=10
# → {"items": [...], "total_count": 1, ...}

# 4. Check index pointer
curl http://localhost:3000/index-pointer
# → {
#      "recent_chain_head": "baguqeera...",
#      "recent_count": 1,
#      "total_count": 1,
#      ...
#    }
```

### Example 2: Build Snapshot and Export

```bash
# 1. Create several entities (A, B, C, D, E)
# ... entity creation steps ...

# 2. Check chain status
curl http://localhost:3000/index-pointer | jq '{recent_count, total_count}'
# → {"recent_count": 5, "total_count": 5}

# 3. Build snapshot
./scripts/build-snapshot.sh
# → [INFO] Walking recent chain from baguqee...
# → [SUCCESS] Collected 5 entries from chain
# → [INFO] Merging entries...
# → [SUCCESS] Total entries to snapshot: 5
# → [SUCCESS] Created 1 chunks in linked list
# → [SUCCESS] Snapshot created: baguqeerakxhfly...

# 4. Verify snapshot structure
SNAP_CID=$(jq -r '.cid' snapshots/latest.json)
docker exec ipfs-node ipfs dag get $SNAP_CID | jq '{schema, seq, total_count, entries_head}'
# → {
#     "schema": "arke/snapshot@v3",
#     "seq": 1,
#     "total_count": 5,
#     "entries_head": {"/": "baguqeerakt..."}
#   }

# 5. Check chunk structure
CHUNK_CID=$(docker exec ipfs-node ipfs dag get $SNAP_CID | jq -r '.entries_head["/"]')
docker exec ipfs-node ipfs dag get $CHUNK_CID | jq '{schema, chunk_index, entries: (.entries | length), prev}'
# → {
#     "schema": "arke/snapshot-chunk@v2",
#     "chunk_index": 0,
#     "entries": 5,
#     "prev": null
#   }

# 6. Export CAR
./scripts/export-car.sh
# → [SUCCESS] CAR file exported: arke-1-20251011-183000.car
# → [INFO] Size: 9.3 MB

# 7. Verify CAR magic bytes
hexdump -C backups/arke-1-*.car | head -1
# → 00000000  0a 01 00 e4 01 71 12 20  ...  |.....q. ...|
#            ^^^ CAR magic bytes
```

### Example 3: Chain Continuity Across Snapshots

```bash
# 1. Create entities A, B, C
# ... creation steps ...

# 2. Build snapshot
./scripts/build-snapshot.sh

# 3. Check pointer AFTER snapshot
curl http://localhost:3000/index-pointer | jq '{recent_chain_head, recent_count}'
# → {
#     "recent_chain_head": "baguqeera...",  ← Still points to C (NOT NULL!)
#     "recent_count": 0                      ← Reset to 0
#   }

# 4. Create entity D
# ... creation steps ...

# 5. Walk full chain
curl "http://localhost:3000/entities?limit=100" | jq '.items[].pi'
# → ["01K75D...", "01K75C...", "01K75B...", "01K75A..."]
#      D (new)     C           B           A
#      ↑ links to C → maintains continuity!
```

### Example 4: Disaster Recovery

```bash
# DISASTER: Server crashed, all data lost!

# 1. Start fresh IPFS node
docker compose down -v  # Nuclear option
docker compose up -d

# 2. Verify empty state
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke 2>&1
# → Error: file does not exist

# 3. Restore from latest CAR
./scripts/restore-from-car.sh backups/arke-2-20251011.car
# → [INFO] Starting CAR restoration...
# → [INFO] Importing CAR file...
# → [SUCCESS] Imported 2315 blocks
# → [INFO] Snapshot CID: baguqeerakxhfly...
# → [INFO] Rebuilding MFS structure from snapshot...
# → [SUCCESS] Rebuilt 2300 .tip files in MFS
# → [SUCCESS] All .tip files verified successfully ✓

# 4. Verify restoration
curl "http://localhost:3000/entities?limit=5" | jq '.items | length'
# → 5

curl http://localhost:3000/index-pointer | jq '.total_count'
# → 2300

# 5. Test entity access
./scripts/verify-entity.sh 01K75GZSKKSP2K6TP05JBFNV09
# → ✓ Entity fully accessible!
```

---

## Performance Characteristics

### Chain Operations

| Operation | Complexity | Typical Time |
|-----------|-----------|--------------|
| Append to chain | O(1) | < 50ms |
| Query recent 10 | O(10) | < 100ms |
| Query recent 100 | O(100) | < 500ms |
| Walk full chain (10K) | O(10,000) | ~13 min |

### Snapshot Operations

| Operation | Complexity | Typical Time |
|-----------|-----------|--------------|
| Build snapshot (5K entities) | O(n) chain walk | ~6 min |
| Build snapshot (10K entities) | O(n) chain walk | ~14 min |
| Export CAR (5K entities) | O(blocks) | ~30 sec |
| Import CAR (5K entities) | O(blocks) | ~1 min |
| Restore MFS (5K entities) | O(n) tips | ~2 min |

### Storage Growth

| Entities | Chain Size | Snapshot Size | CAR Size |
|----------|-----------|---------------|----------|
| 1,000 | ~2 MB | ~2 MB | ~5 MB |
| 5,000 | ~10 MB | ~10 MB | ~20 MB |
| 10,000 | ~20 MB | ~20 MB | ~40 MB |
| 100,000 | ~200 MB | ~200 MB | ~400 MB |

**Note**: Actual sizes depend on entity complexity (components, metadata, etc.)

### Pagination Performance

Cursor-based pagination maintains consistent O(limit) performance:

```
Query recent 10:    O(10)  ← Fast
Query offset 1000:  O(10)  ← Fast (with cursor)
Query offset 5000:  O(10)  ← Fast (with cursor)
```

No offset-based pagination = no need to skip thousands of entries!

---

## Summary

**Key Points**:
1. **PI Chain**: Linked list of all entity operations (creates/updates)
2. **Snapshots**: Duplicative complete-state backups for DR only
3. **Continuity**: `recent_chain_head` never reset to null after snapshot
4. **Queries**: Walk chain with cursor pagination (O(limit) always)
5. **DR**: CAR exports from snapshots contain complete system state
6. **Schemas**: dag-json for chain/snapshots, dag-cbor for manifests

**Architecture Philosophy**:
- Simple over complex
- DR over optimization
- Cursor over offset
- Chains over arrays (consistency)

---

**Document Version**: 1.0
**Last Updated**: 2025-10-11
**Schema Versions**: index-pointer@v1, chain-entry@v1, snapshot@v3, snapshot-chunk@v2, manifest@v1
