# Disaster Recovery (DR) Scripts

Comprehensive backup, restore, and testing infrastructure for the Arke IPFS system.

## Overview

This directory contains all DR-related scripts in **pure Python** for consistency, maintainability, and robust error handling.

## Scripts

### Core DR Operations (All Python)

- **`build-snapshot.py`** - Build snapshot from event chain with deduplication
- **`export-car.py`** - Export snapshot to CAR with explicit CID collection
- **`restore-from-car.py`** - Restore from CAR file
- **`verify-car.py`** - Verify CAR completeness (all manifests, components, events)
- **`verify-entity.sh`** - Verify single entity integrity (legacy bash)

### Testing & Validation

- **`generate-test-data.py`** - Generate controlled test dataset
  - Creates 3 entities with version history
  - Total 6 events in chain (A=3 versions, B=2 versions, C=1 version)
  - Includes components (metadata + image)

- **`verify-snapshot.py`** - Validate snapshot structure
  - Checks for duplicates
  - Verifies CID formats
  - Validates size efficiency (< 500 bytes/entity)

- **`verify-car.py`** - Verify CAR completeness
  - Imports CAR to temp IPFS repo
  - Verifies all manifests (version history)
  - Verifies all components accessible
  - Verifies event chain complete

### Test Infrastructure

- **`docker-compose.test.yml`** (in root) - Isolated test environment
  - Uses different ports (15001, 18080, 14001)
  - Separate volumes (ipfs_test_data, ipfs_test_staging)
  - Safe for nuclear testing without affecting production

## Quick Start

### Running a Full DR Test

```bash
# 1. Start test environment
docker-compose -f docker-compose.test.yml up -d

# 2. Wait for IPFS ready
docker exec ipfs-node-test ipfs id

# 3. Generate test data
IPFS_API_URL=http://localhost:15001/api/v0 \
  ./scripts/dr/generate-test-data.py

# 4. Build snapshot
CONTAINER_NAME=ipfs-node-test \
IPFS_API_URL=http://localhost:15001/api/v0 \
  ./scripts/dr/build-snapshot.py

# 5. Verify snapshot
IPFS_API_URL=http://localhost:15001/api/v0 \
  ./scripts/dr/verify-snapshot.py

# 6. Export CAR
CONTAINER_NAME=ipfs-node-test \
IPFS_API_URL=http://localhost:15001/api/v0 \
  ./scripts/dr/export-car.py

# 7. Verify CAR contents
./scripts/dr/verify-car.py backups/arke-*.car

# 8. Nuclear test - destroy everything
docker-compose -f docker-compose.test.yml down -v

# 9. Start fresh
docker-compose -f docker-compose.test.yml up -d

# 10. Restore from CAR
CONTAINER_NAME=ipfs-node-test \
IPFS_API=http://localhost:15001/api/v0 \
  ./scripts/dr/restore-from-car.py backups/arke-*.car

# 11. Verify restoration
IPFS_API_URL=http://localhost:15001/api/v0 \
  ./scripts/dr/verify-entity.sh ENTITY_A00000000000000
```

### Testing Snapshot Deduplication

The test data is specifically designed to verify deduplication:

```bash
# Event chain has 6 events but only 3 unique PIs
# Snapshot should contain exactly 3 entries:
# - ENTITY_A → v3 tip (latest)
# - ENTITY_B → v2 tip (latest)
# - ENTITY_C → v1 tip (only version)

# Verify deduplication worked:
./scripts/dr/verify-snapshot.py
# Should show: 3 unique PIs, no duplicates
```

### Verifying CAR Completeness

The CAR file should include:
- Snapshot object
- All manifests (6 total: A_v1, A_v2, A_v3, B_v1, B_v2, C_v1)
- All components (metadata + image for A_v3)
- All event chain entries (6 events)

```bash
# After restore, verify version history exists:
IPFS_API=http://localhost:15001/api/v0

# Read A's current tip
docker exec ipfs-node-test ipfs files read /arke/index/EN/TI/ENTITY_A00000000000000.tip

# Get manifest (should be v3)
docker exec ipfs-node-test ipfs dag get <tip_cid>

# Walk backwards to v2
docker exec ipfs-node-test ipfs dag get <v3_prev_cid>

# Walk backwards to v1
docker exec ipfs-node-test ipfs dag get <v2_prev_cid>
```

## Architecture

### Snapshot Structure (v1)

```json
{
  "schema": "arke/snapshot@v1",
  "seq": 1,
  "ts": "2025-10-21T12:00:00Z",
  "event_cid": "baguqeera...",
  "total_count": 3,
  "prev_snapshot": null,
  "entries": [
    {
      "pi": "ENTITY_A00000000000000",
      "ver": 3,
      "tip_cid": {"/": "bafyrei..."},
      "ts": "2025-10-21T12:00:00Z",
      "chain_cid": {"/": "baguqeera..."}
    }
  ]
}
```

### Event Chain Structure

```json
{
  "schema": "arke/event@v1",
  "type": "create",  // or "update"
  "pi": "ENTITY_A00000000000000",
  "ver": 1,
  "tip_cid": {"/": "bafyrei..."},
  "ts": "2025-10-21T12:00:00Z",
  "prev": {"/": "baguqeera..."}  // or null for first event
}
```

## Key Implementation Details

### ⚠️ CRITICAL: input-codec=dag-json Requirement

**All manifests MUST use `input-codec=dag-json` when storing with `ipfs dag put`.**

This ensures IPLD links `{"/": "cid"}` are encoded as **CBOR tag-42** (typed links), not plain maps. Without this:
- ❌ DAG traversal breaks (links not followed)
- ❌ CAR exports incomplete (missing components)
- ❌ Disaster recovery fails

See `DAG_JSON_VS_JSON.md` for complete technical explanation and verification methods.

**Correct Usage:**
```bash
# ✅ CORRECT - Stores links as CBOR tag-42
ipfs dag put --store-codec=dag-cbor --input-codec=dag-json --pin=true

# ❌ WRONG - Stores links as plain maps (breaks DR)
ipfs dag put --store-codec=dag-cbor --input-codec=json --pin=true
```

### Why All Python?

All core DR scripts are now Python for consistency:

**build-snapshot.py:**
- Streams entries to checkpoint file (memory efficient)
- Deduplicates PIs (6 events → 3 entries)
- Uses `ipfs dag put --store-codec=dag-json --input-codec=json` CLI
- **Note**: Snapshots use `store-codec=dag-json` (not dag-cbor) but still need proper link handling
- Supports `--allow-big-block` for large snapshots (>1MB)
- Progress reporting every 100 entries

**export-car.py:**
- **Explicitly collects all CIDs** before export
- Walks version history (prev links) via IPLD link traversal
- Walks event chain
- Verifies all components included via IPLD links
- Reports CID counts by category (DAG nodes vs events)

**verify-car.py:**
- Creates temporary IPFS repo
- Imports CAR and verifies all content accessible
- Walks manifest chains and component links
- Ensures nothing missing

**restore-from-car.py:**
- Imports CAR via docker exec
- Rebuilds MFS .tip files
- Restores index pointer
- Progress reporting during restore

### CAR Export Deep Dive

The `export-car.py` script explicitly walks the snapshot DAG to collect ALL CIDs:
1. **Snapshot CID** - The root
2. **Manifests** - All tip_cid entries + full version history (prev links)
3. **Components** - All metadata, images, etc. from all manifest versions
4. **Events** - All chain_cid entries + full event chain

This ensures `ipfs dag export` has pinned all content and follows all links.

### Restore Process

`restore-from-car.sh` performs:
1. Import CAR blocks
2. Read snapshot object
3. Rebuild MFS .tip files
4. Restore index pointer

## Verification & Testing

### Verify CBOR Tag-42 Encoding

To verify that manifests have proper IPLD link encoding, check for CBOR tag-42 markers:

```bash
# Get manifest as CBOR
docker exec ipfs-node ipfs dag get --output-codec=dag-cbor <manifest_cid> > /tmp/manifest.cbor

# Count CBOR tag-42 occurrences (should match number of IPLD links)
hexdump -C /tmp/manifest.cbor | grep -c "d8 2a"

# Example: Manifest with prev + 2 components should show 3
```

### Verify CAR Export Completeness

```bash
# 1. Export CAR from snapshot
CONTAINER_NAME=ipfs-node python3 scripts/dr/export-car.py

# 2. Check reported CID counts
cat backups/arke-*.json
# Should show: snapshot (1) + dag_nodes (manifests+components) + events

# 3. Import to verify
docker cp backups/arke-*.car ipfs-node:/tmp/test.car
docker exec ipfs-node ipfs dag import --stats /tmp/test.car
# Should import all expected blocks
```

### Test with Fresh Environment

```bash
# 1. Clean test environment
docker-compose -f docker-compose.test.yml down -v
docker-compose -f docker-compose.test.yml up -d

# 2. Add test data via API (uses correct codec automatically)
# Via http://localhost:3001

# 3. Build snapshot
IPFS_API_URL=http://localhost:15001/api/v0 \
  CONTAINER_NAME=ipfs-node-test \
  python3 scripts/dr/build-snapshot.py

# 4. Export CAR
IPFS_API_URL=http://localhost:15001/api/v0 \
  CONTAINER_NAME=ipfs-node-test \
  python3 scripts/dr/export-car.py

# 5. Verify CBOR encoding
docker exec ipfs-node-test sh -c \
  'ipfs dag get --output-codec=dag-cbor <manifest_cid> > /tmp/m.cbor && \
   hexdump -C /tmp/m.cbor | grep -c "d8 2a"'
```

## Success Criteria

✅ Snapshot is minimal (< 500 bytes/entity)
✅ Snapshot has no duplicates
✅ CAR includes all manifests (version history)
✅ CAR includes all components (metadata, images)
✅ CAR includes event chain
✅ Manifests have CBOR tag-42 markers (proper IPLD links)
✅ CAR import shows expected block count
✅ Restore is idempotent
✅ Verification passes

## Troubleshooting

### CAR export missing components (incomplete)

**Symptom**: CAR file imports fewer blocks than expected, components missing

**Cause**: Manifests stored with `input-codec=json` instead of `dag-json`

**Diagnosis**:
```bash
# Check for CBOR tag-42 markers
docker exec ipfs-node sh -c 'ipfs dag get --output-codec=dag-cbor <manifest_cid> > /tmp/m.cbor && hexdump -C /tmp/m.cbor | grep -c "d8 2a"'

# Should return count matching number of IPLD links
# If returns 0 or too few, manifests have fake links
```

**Fix**: Re-create manifests using correct codec (see `DAG_JSON_VS_JSON.md`)

### "No such file or directory" errors

Ensure you're using the test environment variables:
```bash
export IPFS_API_URL=http://localhost:15001/api/v0
export CONTAINER_NAME=ipfs-node-test
```

### CAR export hangs

Large datasets may need longer timeout. The export script uses 1 hour timeout.

### Restore fails with "invalid CID"

Check that the CAR file was created correctly:
```bash
xxd -l 64 backups/arke-*.car  # Should show CAR header
```

### Version history missing after restore

Verify CAR was exported from snapshot CID (not manifest CID):
```bash
# Correct: ipfs dag export <snapshot_cid>
# Wrong: ipfs dag export <manifest_cid>
```

### Snapshot too large (>1MB)

**Symptom**: `ipfs dag put` fails with "produced block is over 1MiB"

**Fix**: Already implemented in `build-snapshot.py` - uses `--allow-big-block` flag

## Production Usage

For production systems, use the scripts in `../` (parent directory):
- `daily-car-export.sh` - Automated daily backups
- `cleanup-old-snapshots.sh` - Retention management

These scripts reference the DR scripts in this directory.
