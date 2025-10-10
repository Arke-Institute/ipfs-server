# Disaster Recovery Test Procedure

Complete step-by-step guide for testing CAR-based disaster recovery, including the **nuclear option** (destroying volumes and restoring from CAR alone).

---

## Overview

This test validates that:
1. ✅ We can capture current system state in a snapshot
2. ✅ We can export to a single CAR file
3. ✅ We can destroy the IPFS datastore completely
4. ✅ We can restore **everything** from the CAR alone
5. ✅ All entities, versions, and components are intact

**Estimated Time**: 15-20 minutes

---

## Prerequisites

- IPFS node running with test data
- All scripts executable (`chmod +x scripts/*.sh`)
- At least 4 entities with `.tip` files in `/arke/index/`

**Check prerequisites**:
```bash
# Node running?
docker compose ps

# Data present?
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index | jq '.Entries | length'
```

---

## Phase 1: Baseline Verification

**Goal**: Document current state before any changes.

### Step 1.1: Count Entities

```bash
# List all tip files
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index/01/K7 | jq '.Entries'
```

**Record**: Number of `.tip` files found: ____________

### Step 1.2: Record Sample Entity

Pick one entity to verify end-to-end:

```bash
# Read tip
PI="01K75GZSKKSP2K6TP05JBFNV09"  # Replace with actual PI
TIP_CID=$(curl -s -X POST "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/K7/${PI}.tip" | tr -d '\n')
echo "Tip CID: $TIP_CID"

# Get manifest
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$TIP_CID" | jq .
```

**Record**:
- PI: ____________
- Tip CID: ____________
- Version: ____________
- Has prev link? ____________

### Step 1.3: Verify Version History

```bash
# Walk history
CURRENT=$TIP_CID
while [[ -n "$CURRENT" ]]; do
  echo "=== Version ==="
  MANIFEST=$(curl -s -X POST "http://localhost:5001/api/v0/dag/get?arg=$CURRENT")
  echo "$MANIFEST" | jq '{ver, ts, prev}'
  CURRENT=$(echo "$MANIFEST" | jq -r '.prev["/"] // empty')
done
```

**Record**: Number of versions in history: ____________

### Step 1.4: Snapshot Repository Stats

```bash
curl -X POST http://localhost:5001/api/v0/repo/stat | jq '{RepoSize, NumObjects, StorageMax}'
```

**Record**:
- Repo Size: ____________ bytes
- Num Objects: ____________

---

## Phase 2: Build Snapshot & Export CAR

**Goal**: Create backup artifacts.

### Step 2.1: Build Snapshot

```bash
./scripts/build-snapshot.sh
```

**Expected Output**:
```
[INFO] Building snapshot index...
[SUCCESS] Found 1 .tip files
...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot Build Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CID:      baguqeeraz...
Sequence: 1
Entities: 1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Note: Snapshot CID will start with `baguqee...` (dag-json format)

**Record**:
- Snapshot CID: ____________
- Sequence: ____________
- Entity count matches Phase 1? ☐ Yes ☐ No

**Verify snapshot object** (should be dag-json format):
```bash
SNAP_CID=$(jq -r '.cid' snapshots/latest.json)
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq .

# Verify CID starts with 'baguqee' (dag-json prefix)
echo "$SNAP_CID" | grep -q '^baguqee' && echo "✓ dag-json format" || echo "✗ Wrong format!"
```

### Step 2.2: Export CAR

```bash
./scripts/export-car.sh
```

**Expected Output**:
```
[INFO] Exporting snapshot to CAR...
...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAR Export Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
File:      arke-1-20251010-023429.car
Size:      0.42 MB
Location:  ./backups/arke-1-20251010-023429.car
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Note: CAR size will be much larger than snapshot (includes all blocks in graph)

**Record**:
- CAR filename: ____________
- CAR size: ____________ MB

**Verify CAR file exists**:
```bash
ls -lh backups/*.car
```

---

## Phase 3: Nuclear Test (Destroy & Restore)

**⚠️ WARNING**: This will delete all IPFS data! Only proceed if you're ready.

### Step 3.1: Stop Node

```bash
docker compose down
```

**Verify stopped**:
```bash
docker compose ps
# Should show no containers
```

### Step 3.2: Delete Volumes (Nuclear Option)

```bash
docker volume ls | grep ipfs-server
```

**Record volumes to delete**:
- ____________
- ____________

**DELETE VOLUMES**:
```bash
docker volume rm ipfs-server_ipfs_data
docker volume rm ipfs-server_ipfs_staging
```

**Verify deletion**:
```bash
docker volume ls | grep ipfs-server
# Should show nothing
```

🔥 **DATA DELETED** 🔥

### Step 3.3: Restart Fresh Node

```bash
docker compose up -d
```

**Wait for initialization** (~30 seconds):
```bash
docker compose logs -f
# Wait for "Daemon is ready"
# Press Ctrl+C when ready
```

**Verify fresh state**:
```bash
# Should be empty!
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke 2>&1
# Expected: Error (file does not exist)

# Check repo stats (should be minimal)
curl -X POST http://localhost:5001/api/v0/repo/stat | jq '{RepoSize, NumObjects}'
```

**Record**:
- Fresh repo size: ____________ (should be ~100KB)
- Fresh object count: ____________ (should be < 10)

---

## Phase 4: Restore from CAR

**Goal**: Restore everything from CAR file alone.

### Step 4.1: Run Restore Script

```bash
CAR_FILE=$(ls -t backups/*.car | head -1)
echo "Restoring from: $CAR_FILE"

./scripts/restore-from-car.sh "$CAR_FILE"
```

**Expected Output**:
```
[INFO] Starting CAR restoration...
[INFO] Importing CAR file: arke-1-20251010-023429.car
...
[SUCCESS] CAR file imported successfully
[SUCCESS] Imported 6 blocks (445930 bytes)
[INFO] Snapshot CID: baguqeeraz...
[INFO] Rebuilding MFS structure from snapshot...
[1/1] 01K75ZA2RHKTJ3GDND0ZK46M4G (v1)
  ✓ Created: /arke/index/01/K7/01K75ZA2RHKTJ3GDND0ZK46M4G.tip → baguqeerayq...
...
[SUCCESS] Rebuilt 1 .tip files in MFS
[SUCCESS] All .tip files verified successfully ✓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Restoration Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot:   baguqeeraz... (seq 1)
Entities:   1
MFS:        /arke/index
Status:     ✓ All verified
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

System restored from CAR file! Ready to serve requests.
```

**Important**: The script automatically rebuilds MFS from the snapshot. No manual intervention needed!

**Record**:
- Blocks imported: ____________
- Entities restored: ____________
- Verification passed? ☐ Yes ☐ No

---

## Phase 5: Verification

**Goal**: Prove restored system matches original.

### Step 5.1: Count Entities

```bash
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index/01/K7 | jq '.Entries | length'
```

**Record**: Entity count: ____________

**Compare**: Does it match Phase 1.1? ☐ Yes ☐ No

### Step 5.2: Verify Sample Entity

Use the same PI from Phase 1.2:

```bash
PI="01K75ZA2RHKTJ3GDND0ZK46M4G"  # Use your recorded PI

# Use verify-entity.sh script
./scripts/verify-entity.sh "$PI"
```

Or manually:

```bash
TIP_CID=$(curl -s -X POST "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/K7/${PI}.tip" | tr -d '\n')
echo "Restored Tip CID: $TIP_CID"

# Get manifest
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$TIP_CID" | jq .
```

**Compare**:
- Tip CID matches Phase 1.2? ☐ Yes ☐ No
- Manifest structure identical? ☐ Yes ☐ No
- Entity fully accessible (metadata, images)? ☐ Yes ☐ No

### Step 5.3: Verify Version History

```bash
# Walk history (same as Phase 1.3)
CURRENT=$TIP_CID
VERSION_COUNT=0
while [[ -n "$CURRENT" ]]; do
  VERSION_COUNT=$((VERSION_COUNT + 1))
  MANIFEST=$(curl -s -X POST "http://localhost:5001/api/v0/dag/get?arg=$CURRENT")
  echo "$MANIFEST" | jq '{ver, ts, prev}'
  CURRENT=$(echo "$MANIFEST" | jq -r '.prev["/"] // empty')
done

echo "Total versions: $VERSION_COUNT"
```

**Compare**: Version count matches Phase 1.3? ☐ Yes ☐ No

### Step 5.4: Verify Component Retrieval

```bash
# Get a component CID from manifest
COMPONENT_CID=$(curl -s -X POST "http://localhost:5001/api/v0/dag/get?arg=$TIP_CID" | jq -r '.components.data["/"]')
echo "Component CID: $COMPONENT_CID"

# Retrieve component content
curl -X POST "http://localhost:5001/api/v0/cat?arg=$COMPONENT_CID"
```

**Result**: Component retrieved successfully? ☐ Yes ☐ No

### Step 5.5: Check Repository Stats

```bash
curl -X POST http://localhost:5001/api/v0/repo/stat | jq '{RepoSize, NumObjects}'
```

**Record**:
- Restored repo size: ____________
- Restored object count: ____________

**Compare**:
- Repo size similar to Phase 1.4? ☐ Yes ☐ No
- Object count similar to Phase 1.4? ☐ Yes ☐ No

---

## Phase 6: Test New Operations

**Goal**: Prove system is fully functional.

### Step 6.1: Create New Version

```bash
PI="01K75GZSKKSP2K6TP05JBFNV09"  # Use existing PI

# Get current tip (for CAS)
CURRENT_TIP=$(curl -s -X POST "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/K7/${PI}.tip" | tr -d '\n')
echo "Current tip: $CURRENT_TIP"

# Upload new data
NEW_DATA_CID=$(echo '{"updated": "after restore"}' | curl -s -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/add?quieter=true&cid-version=1" | jq -r '.Hash')
echo "New data CID: $NEW_DATA_CID"

# Get old manifest
OLD_MANIFEST=$(curl -s -X POST "http://localhost:5001/api/v0/dag/get?arg=$CURRENT_TIP")
OLD_VER=$(echo "$OLD_MANIFEST" | jq -r '.ver')
NEW_VER=$((OLD_VER + 1))

# Create new manifest
NEW_MANIFEST=$(jq -n \
  --arg schema "arke/manifest@v1" \
  --arg pi "$PI" \
  --argjson ver "$NEW_VER" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg prev_cid "$CURRENT_TIP" \
  --arg data_cid "$NEW_DATA_CID" \
  '{
    schema: $schema,
    pi: $pi,
    ver: $ver,
    ts: $ts,
    prev: {"/": $prev_cid},
    components: {data: {"/": $data_cid}},
    note: "Created after restore to test append"
  }')

echo "$NEW_MANIFEST" | jq .

# Store new manifest
NEW_MANIFEST_CID=$(echo "$NEW_MANIFEST" | curl -s -X POST \
  -H "Content-Type: application/json" \
  -d @- \
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true" | jq -r '.Cid["/"]')

echo "New manifest CID: $NEW_MANIFEST_CID"

# Update tip
echo "$NEW_MANIFEST_CID" | curl -s -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/arke/index/01/K7/${PI}.tip&create=true&truncate=true"

echo "✓ New version created"
```

**Result**: Successfully created v3? ☐ Yes ☐ No

### Step 6.2: Verify New Version in History

```bash
CURRENT=$NEW_MANIFEST_CID
while [[ -n "$CURRENT" ]]; do
  MANIFEST=$(curl -s -X POST "http://localhost:5001/api/v0/dag/get?arg=$CURRENT")
  echo "$MANIFEST" | jq '{ver, ts, note}'
  CURRENT=$(echo "$MANIFEST" | jq -r '.prev["/"] // empty')
done
```

**Result**: Can walk from v3 → v2 → v1? ☐ Yes ☐ No

---

## Results

### Test Summary

| Phase | Test | Result |
|-------|------|--------|
| 1.1 | Count entities (baseline) | ☐ Pass ☐ Fail |
| 1.2 | Record sample entity | ☐ Pass ☐ Fail |
| 1.3 | Verify version history | ☐ Pass ☐ Fail |
| 2.1 | Build snapshot | ☐ Pass ☐ Fail |
| 2.2 | Export CAR | ☐ Pass ☐ Fail |
| 3.2 | **Delete volumes (nuclear)** | ☐ Completed |
| 4.1 | Restore from CAR | ☐ Pass ☐ Fail |
| 5.1 | Entity count matches | ☐ Pass ☐ Fail |
| 5.2 | Sample entity matches | ☐ Pass ☐ Fail |
| 5.3 | Version history intact | ☐ Pass ☐ Fail |
| 5.4 | Components retrievable | ☐ Pass ☐ Fail |
| 5.5 | Repo stats similar | ☐ Pass ☐ Fail |
| 6.1 | Create new version | ☐ Pass ☐ Fail |
| 6.2 | New version in history | ☐ Pass ☐ Fail |

### Success Criteria

**DR test passes if**:
- ✅ All Phase 5 verifications match Phase 1
- ✅ All `.tip` files restored correctly
- ✅ All version histories intact
- ✅ All components retrievable
- ✅ New operations work (Phase 6)

**Overall Result**: ☐ PASS ☐ FAIL

---

## Troubleshooting

### Restore Failed

**Symptom**: Step 4.1 fails or verifies fail

**Debug**:
```bash
# Check what was imported
curl -X POST http://localhost:5001/api/v0/pin/ls | jq '.Keys | keys | length'

# Check snapshot object
SNAP_CID=$(jq -r '.cid' snapshots/latest.json)
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq .

# Manually check one tip
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index/01/K7
```

### Version Count Mismatch

**Symptom**: Fewer versions after restore

**Cause**: CAR export didn't include full history (missing IPLD links)

**Fix**: Check manifests use `{"/": "cid"}` format for `prev` field

---

## Cleanup

After successful test:

```bash
# Optional: Build new snapshot including v3
./scripts/build-snapshot.sh

# Optional: Export new CAR
./scripts/export-car.sh
```

---

## Next Steps

1. ☐ Document test results
2. ☐ Set up automated backup schedule
3. ☐ Configure offsite storage
4. ☐ Create runbook for team
5. ☐ Schedule quarterly DR drills

---

**Test Completed By**: Claude Code
**Date**: 2025-10-10
**Duration**: 20 minutes
**Overall Result**: ✓ PASS

---

**Actual Test Results**:
- Complete data destruction confirmed (all volumes deleted)
- CAR import: 6 blocks (446KB)
- MFS rebuild: automatic, no manual intervention
- Verification: All .tip files created correctly
- Entity accessibility: Full end-to-end access confirmed (manifest, metadata, images)
- System fully operational after restore

**Key Learnings**:
1. dag-json codec is critical for IPLD link preservation
2. Logging to stderr prevents stdout contamination in bash functions
3. Array indexing more reliable than bash streaming for parsing
4. MFS must be rebuilt from snapshot (not included in CAR)
5. Single CAR file contains complete system state for full recovery

---

**Notes**:
