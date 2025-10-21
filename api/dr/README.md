# Disaster Recovery (DR) Module

Python-based disaster recovery system for the Arke IPFS infrastructure. This module provides snapshot-based backups, CAR file exports, and complete system restoration.

## Overview

The DR system runs **inside the `ipfs-api` container** and has access to:
- Python 3.11 runtime with httpx library
- Docker CLI for executing commands in `ipfs-node` container
- IPFS HTTP API at `http://ipfs:5001/api/v0`

This design solves the production deployment challenge: DR scripts can run in any environment (local dev, EC2, Docker) without requiring Python installation on the host system.

## Architecture

### Snapshot-Based Backup

The system uses **snapshots** as the foundation for backups:

1. **Event Chain** → Continuous log of all creates/updates
2. **Snapshot** → Point-in-time index of latest entity versions (deduplicated)
3. **CAR Export** → Portable archive containing snapshot + all referenced content

```
Event Chain (6 events)
  create A v1 → update A v2 → update A v3
  create B v1 → update B v2
  create C v1

Snapshot (3 entries - deduplicated)
  A → v3 tip
  B → v2 tip
  C → v1 tip

CAR File (complete backup)
  ├─ Snapshot object
  ├─ All manifests (A v1-3, B v1-2, C v1)
  ├─ All components (metadata, images, etc.)
  └─ Event chain
```

### Data Model

**Snapshot Structure** (`arke/snapshot@v1`):
```json
{
  "schema": "arke/snapshot@v1",
  "seq": 1,
  "ts": "2025-10-21T12:00:00Z",
  "event_cid": "baguqeera...",
  "total_count": 3,
  "prev_snapshot": {"\/": "baguqeera..."},
  "entries": [
    {
      "pi": "ENTITY_A00000000000000",
      "ver": 3,
      "tip_cid": {"\/": "bafyrei..."},
      "ts": "2025-10-21T12:00:00Z",
      "chain_cid": {"\/": "baguqeera..."}
    }
  ]
}
```

## Module Files

### Core DR Operations

#### `build_snapshot.py`
Builds a snapshot from the event chain with deduplication.

**What it does:**
- Walks event chain backwards from head
- Deduplicates by PI (only keeps latest version per entity)
- Writes entries to checkpoint file incrementally (memory efficient)
- Stores snapshot as `dag-json` using `ipfs dag put` CLI
- Updates index pointer with new snapshot CID

**Usage:**
```bash
# Inside ipfs-api container
python3 -m dr.build_snapshot

# Or via docker exec
docker exec ipfs-api python3 -m dr.build_snapshot
```

**Environment Variables:**
- `IPFS_API_URL` - IPFS HTTP API endpoint (default: `http://localhost:5001/api/v0`)
- `CONTAINER_NAME` - IPFS container name for docker exec (default: `ipfs-node`)
- `SNAPSHOTS_DIR` - Directory for snapshot metadata (default: `./snapshots`)

**Output:**
- Snapshot CID to stdout
- Progress logs to stderr
- Metadata files: `snapshots/snapshot-{seq}.json`, `snapshots/latest.json`

**Performance:**
- Streaming approach handles large datasets (31k+ entities tested)
- Progress reporting every 100 entries
- Uses `--allow-big-block` for snapshots >1MB

---

#### `export_car.py`
Exports snapshot to CAR (Content Addressable aRchive) file.

**What it does:**
- Reads latest snapshot from index pointer
- **Explicitly collects ALL CIDs** by walking:
  - Snapshot object
  - All manifest tip_cids + version history (prev links)
  - All components referenced by manifests
  - Full event chain (chain_cid + prev links)
- Pins all collected CIDs
- Exports to CAR using `ipfs dag export` CLI
- Creates metadata file with CID inventory

**Usage:**
```bash
docker exec ipfs-api python3 -m dr.export_car
```

**Environment Variables:**
- `IPFS_API_URL` - IPFS HTTP API endpoint
- `CONTAINER_NAME` - IPFS container name for docker exec
- `BACKUPS_DIR` - Directory for CAR files (default: `./backups`)

**Output:**
- CAR file: `backups/arke-{seq}-{timestamp}.car`
- Metadata: `backups/arke-{seq}-{timestamp}.json` with CID counts

**Important:**
- CAR export requires all content to be pinned
- Export collects CIDs recursively to ensure completeness
- Timeout: 1 hour for large datasets

---

#### `restore_from_car.py`
Restores complete system state from CAR backup.

**What it does:**
1. Waits for IPFS node to be ready (health check)
2. Imports CAR blocks using `ipfs dag import`
3. Fetches snapshot object
4. Rebuilds MFS `.tip` files for all entities
5. Restores index pointer
6. Verifies all .tip files created

**Usage:**
```bash
# Auto-detect snapshot CID from metadata
docker exec ipfs-api python3 -m dr.restore_from_car backups/arke-1-*.car

# Or specify snapshot CID explicitly
docker exec ipfs-api python3 -m dr.restore_from_car backups/arke-1-*.car baguqeera...
```

**Environment Variables:**
- `IPFS_API_URL` - IPFS HTTP API endpoint
- `CONTAINER_NAME` - IPFS container name for docker exec

**Output:**
- JSON result to stdout: `{"snapshot_cid": "...", "entity_count": 3, "restored": true}`
- Progress logs to stderr

**Important:**
- Waits up to 60 seconds for IPFS node to be ready
- Safe to run on fresh/empty IPFS node
- Idempotent - can re-run without issues

---

### Verification & Testing

#### `verify_snapshot.py`
Validates snapshot structure and efficiency.

**What it does:**
- Fetches latest snapshot from IPFS
- Checks for duplicate PIs
- Verifies CID format validity
- Calculates size efficiency (bytes per entity)

**Usage:**
```bash
docker exec ipfs-api python3 -m dr.verify_snapshot
```

**Success Criteria:**
- No duplicate PIs
- Valid CID formats
- Size < 500 bytes/entity

---

#### `verify_car.py`
Verifies CAR file completeness (requires standalone ipfs binary).

**What it does:**
- Creates temporary IPFS repo
- Imports CAR
- Verifies all manifests accessible
- Checks all components reachable
- Validates event chain complete

**Usage:**
```bash
# Run on host (not in container)
./api/dr/verify_car.py backups/arke-1-*.car
```

**Note:** This script is NOT used in production - Test 6 (nuclear test) validates CAR integrity instead.

---

#### `generate_test_data.py`
Generates controlled test dataset for DR testing.

**What it does:**
- Creates 3 test entities (A, B, C) with version history
- Entity A: 3 versions (with components in v3)
- Entity B: 2 versions
- Entity C: 1 version
- Total: 6 events in chain

**Usage:**
```bash
docker exec ipfs-api python3 -m dr.generate_test_data
```

**Output:**
- Creates entities via IPFS API
- Reports PIs and event chain structure

---

## Integration Points

### Auto-Snapshot Scheduler

The `api/events.py` module triggers scheduled snapshots:

```python
async def trigger_scheduled_snapshot():
    """Triggered by scheduler every N minutes."""
    # ... checks for lock file, entity count, etc. ...

    # Run snapshot build in background
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_snapshot_build, ...)

def _run_snapshot_build(trigger_time, total_pis, total_events):
    """Run snapshot build in thread pool."""
    subprocess.run(
        ["python3", "-m", "dr.build_snapshot"],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd="/app",
        check=False
    )
```

Logs are written to `/app/logs/snapshot-build.log`.

### Docker Socket Access

The `ipfs-api` container has Docker socket mounted to execute commands in `ipfs-node`:

```yaml
# docker-compose.yml
ipfs-api:
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
  environment:
    - CONTAINER_NAME=ipfs-node
```

This enables:
- `docker exec ipfs-node ipfs dag put ...`
- `docker exec ipfs-node ipfs dag export ...`
- `docker cp` for CAR files

## Testing

### Local Testing (Validated)

All tests run successfully using local Docker environment:

**Test 1: Container Setup**
```bash
docker compose build
docker compose up -d
docker exec ipfs-api python3 -c "import dr; import docker; print('OK')"
```

**Test 2: Generate Test Data**
```bash
docker exec ipfs-api python3 -m dr.generate_test_data
```

**Test 3: Build Snapshot**
```bash
docker exec ipfs-api python3 -m dr.build_snapshot
```

**Test 4: Export CAR**
```bash
docker exec ipfs-api python3 -m dr.export_car
ls -lh backups/arke-*.car
```

**Test 5: Verify Snapshot**
```bash
docker exec ipfs-api python3 -m dr.verify_snapshot
```

**Test 6: Nuclear Test (Complete DR Cycle)**
```bash
# Wipe everything
docker compose down -v

# Start fresh
docker compose up -d

# Restore from CAR
docker exec ipfs-api python3 -m dr.restore_from_car backups/arke-1-*.car

# Verify entities accessible
docker exec ipfs-api python3 -c "
import httpx
response = httpx.post('http://ipfs:5001/api/v0/files/read',
                      params={'arg': '/arke/index/EN/TI/ENTITY_A00000000000000.tip'})
print(f'Restored: {response.text.strip()[:16]}...')
"
```

**Test 7: Auto-Snapshot Scheduler**
```bash
docker exec ipfs-api python3 -c "
import asyncio
import sys
sys.path.insert(0, '/app')
from events import trigger_scheduled_snapshot
asyncio.run(trigger_scheduled_snapshot())
"

# Check log
docker exec ipfs-api cat /app/logs/snapshot-build.log
```

### Validation Results

All 7 tests pass with complete verification:
- ✅ Snapshot builds successfully
- ✅ CAR exports with all content (manifests, components, events)
- ✅ Full restoration from CAR after complete data wipe
- ✅ Version history intact (all prev links preserved)
- ✅ Components accessible (metadata, images)
- ✅ Auto-snapshot integration working
- ✅ Index pointer updates correctly

## Important Caveats

### 1. IPLD Link Encoding (CRITICAL)

**All manifests MUST use `input-codec=dag-json` when storing with `ipfs dag put`.**

This ensures IPLD links `{"/": "cid"}` are encoded as CBOR tag-42 (typed links), not plain maps.

```bash
# ✅ CORRECT - Stores links as CBOR tag-42
docker exec ipfs-node ipfs dag put \
  --store-codec=dag-cbor \
  --input-codec=dag-json \
  --pin=true

# ❌ WRONG - Stores links as plain maps (breaks CAR export)
docker exec ipfs-node ipfs dag put \
  --store-codec=dag-cbor \
  --input-codec=json \
  --pin=true
```

**Why it matters:**
- Without proper encoding, DAG traversal breaks
- CAR exports become incomplete (missing components)
- Disaster recovery fails

See `DAG_JSON_VS_JSON.md` (in this directory) for full technical details on IPLD link encoding.

### 2. Snapshot Codec (dag-json vs dag-cbor)

Snapshots use `--store-codec=dag-json` (NOT dag-cbor) because:
- CAR exporters require dag-json to follow IPLD links correctly
- Enables proper recursive CID collection
- Verified working in Test 4 & Test 6

### 3. Lock File Protection

`build_snapshot.py` uses lock file `/tmp/arke-snapshot.lock` to prevent concurrent builds:
- Lock expires after 10 minutes (stale lock cleanup)
- Contains PID and timestamp
- Prevents race conditions in event chain walking

### 4. Memory Efficiency

Snapshot building uses streaming approach:
- Writes entries to checkpoint file incrementally
- Avoids loading entire dataset in memory
- Tested with 31k+ entities successfully

### 5. Environment Variable Consistency

All scripts check for **both** `IPFS_API_URL` and `IPFS_API`:
```python
IPFS_API = os.getenv("IPFS_API_URL", os.getenv("IPFS_API", "http://localhost:5001/api/v0"))
```

This ensures compatibility with:
- `docker-compose.yml` (sets `IPFS_API_URL`)
- Legacy scripts (may use `IPFS_API`)
- Manual invocations (fallback to localhost)

### 6. Health Check Wait

`restore_from_car.py` includes IPFS readiness check:
- Waits up to 60 seconds for IPFS node to respond
- Prevents "Cannot assign requested address" errors
- Critical for fresh container starts

## Production Deployment

### Prerequisites

1. **Docker Socket Access**: Host must allow Docker socket mounting
2. **Container Permissions**: `ipfs-api` container needs Docker CLI access
3. **Storage**: Sufficient space for CAR files in `backups/` directory

### Deployment Steps

1. **Build and Deploy Containers**
   ```bash
   docker compose -f docker-compose.prod.yml build
   docker compose -f docker-compose.prod.yml up -d
   ```

2. **Verify DR Module**
   ```bash
   docker exec ipfs-api python3 -c "import dr; print('DR module loaded')"
   ```

3. **Initial Snapshot** (if entities exist)
   ```bash
   docker exec ipfs-api python3 -m dr.build_snapshot
   ```

4. **Set Up Automated Backups** (via cron or scheduled task)
   ```bash
   # Daily snapshot at 2 AM
   0 2 * * * docker exec ipfs-api python3 -m dr.build_snapshot >> /var/log/arke-snapshot.log 2>&1

   # Daily CAR export at 3 AM
   0 3 * * * docker exec ipfs-api python3 -m dr.export_car >> /var/log/arke-car-export.log 2>&1
   ```

5. **Set Up Monitoring**
   - Check snapshot build logs: `/app/logs/snapshot-build.log`
   - Monitor CAR file creation: `ls -lh /app/backups/`
   - Verify latest snapshot metadata: `cat /app/snapshots/latest.json`

### Recovery Procedure

In case of data loss:

1. **Obtain Latest CAR File**
   ```bash
   ls -lht backups/arke-*.car | head -1
   ```

2. **Stop Containers** (if running)
   ```bash
   docker compose down
   ```

3. **Wipe Corrupted Data** (if necessary)
   ```bash
   docker compose down -v  # Removes volumes
   ```

4. **Start Fresh Containers**
   ```bash
   docker compose up -d
   ```

5. **Restore from CAR**
   ```bash
   docker exec ipfs-api python3 -m dr.restore_from_car backups/arke-{seq}-{timestamp}.car
   ```

6. **Verify Restoration**
   ```bash
   docker exec ipfs-api python3 -c "
   import httpx
   import json
   response = httpx.post('http://ipfs:5001/api/v0/files/read',
                         params={'arg': '/arke/index-pointer'})
   pointer = response.json()
   print(json.dumps(pointer, indent=2))
   "
   ```

## Troubleshooting

### "Cannot assign requested address" Error

**Cause:** IPFS node not fully ready when restore attempted

**Fix:** The script now includes automatic health check wait. If still occurring, manually wait:
```bash
docker exec ipfs-node ipfs id  # Wait until this succeeds
```

### "Snapshot build already in progress"

**Cause:** Lock file exists from previous run

**Fix:**
```bash
docker exec ipfs-api rm -f /tmp/arke-snapshot.lock
```

### CAR Export Missing Components

**Cause:** Manifests stored with wrong codec (input-codec=json instead of dag-json)

**Diagnosis:**
```bash
# Check for CBOR tag-42 markers
docker exec ipfs-node sh -c 'ipfs dag get --output-codec=dag-cbor <manifest_cid> > /tmp/m.cbor && hexdump -C /tmp/m.cbor | grep -c "d8 2a"'
```

**Fix:** Re-create manifests using correct codec (via API)

### "No such file or directory: 'docker'"

**Cause:** Docker CLI not installed in ipfs-api container

**Fix:** Rebuild container (Dockerfile includes `docker.io` package)

## Summary

The DR module provides:
- ✅ **Automated snapshots** via scheduler integration
- ✅ **Portable backups** via CAR files
- ✅ **Complete restoration** tested with nuclear wipe
- ✅ **Production-ready** runs in container, no host dependencies
- ✅ **Memory efficient** streaming approach for large datasets
- ✅ **Fully tested** 7 comprehensive tests all passing

For production deployment questions or issues, refer to the testing section or contact the development team.
