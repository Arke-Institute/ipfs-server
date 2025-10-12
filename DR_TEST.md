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

**Goal**: Create backup artifacts with chain head preservation.

### Step 2.1: Build Snapshot

```bash
./scripts/build-snapshot.sh
```

**Expected Output**:
```
[INFO] Building snapshot index...
[SUCCESS] Found 3 .tip files
[INFO] Walking chain from head...
...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot Build Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CID:      baguqeeraz...
Sequence: 2
Entities: 3
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

**⚠️ CRITICAL: Verify chain_cid and tip_cid are IPLD link format**:
```bash
# Fetch snapshot and verify field formats
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq '.entries[0] | {tip_cid, chain_cid}'

# Expected:
# {
#   "tip_cid": {"/": "bafyrei..."},     ✓ CORRECT
#   "chain_cid": {"/": "baguqee..."}    ✓ CORRECT
# }
#
# NOT:
# {
#   "tip_cid": "bafyrei...",            ✗ WRONG - manifests won't be in CAR!
#   "chain_cid": "baguqee..."           ✗ WRONG - chain entries won't be in CAR!
# }
```

### Step 2.2: Export CAR

```bash
./scripts/export-car.sh
```

**Expected Output**:
```
[INFO] Exporting snapshot to CAR...
[INFO] Running: ipfs dag export baguqee... (with 60s timeout)
...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAR Export Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
File:      arke-2-20251012-030530.car
Size:      1.8 KB
Location:  ./backups/arke-2-20251012-030530.car
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Note on timing**:
- Small archives (< 10 entities) export in < 5 seconds
- The script uses 60s timeout as `ipfs dag export` may not exit cleanly
- File validation happens after timeout, checking size and existence

**Record**:
- CAR filename: ____________
- CAR size: ____________ KB or MB

**Verify CAR file exists and has content**:
```bash
ls -lh backups/*.car
# Should see non-zero file size

# Verify CAR contains chain entry blocks (critical!)
CAR_FILE=$(ls -t backups/*.car | head -1)
echo "Verifying chain entries are in CAR..."
# This will be validated in Phase 5 when we test /entities endpoint
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

**Goal**: Prove restored system matches original AND chain head is preserved.

### Step 5.0: ⚠️ CRITICAL - Verify /entities Endpoint Works

**This validates that chain entries were included in CAR!**

```bash
# Test the /entities API endpoint
curl -s 'http://localhost:3000/entities?limit=10' | jq .
```

**Expected Output**:
```json
{
  "items": [
    {
      "pi": "ENTITY_3",
      "ver": 1,
      "tip": "bafyrei...",
      "ts": "2025-10-12T03:05:17.899637Z"
    },
    {
      "pi": "ENTITY_2",
      "ver": 1,
      "tip": "bafyreic...",
      "ts": "2025-10-12T03:05:17.835055Z"
    },
    {
      "pi": "ENTITY_1",
      "ver": 1,
      "tip": "bafyreid...",
      "ts": "2025-10-12T03:05:17.769678Z"
    }
  ],
  "total_count": 3,
  "has_more": false,
  "next_cursor": null
}
```

**⚠️ CRITICAL CHECK**:
- If endpoint returns `{"items": [], "total_count": 0}` → Chain entry blocks are MISSING!
- This means `chain_cid` was stored as plain string, not IPLD link
- See troubleshooting section in DISASTER_RECOVERY.md

**Verify index pointer has recent_chain_head**:
```bash
curl -X POST http://localhost:5001/api/v0/files/read?arg=/arke/index-pointer | jq .

# Should show:
# {
#   "recent_chain_head": {"/":" "baguqee..."},  ← NOT null!
#   "latest_snapshot_cid": "baguqee...",
#   ...
# }
```

**Result**:
- `/entities` returns all entities? ☐ Yes ☐ No
- `recent_chain_head` is not null? ☐ Yes ☐ No

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
| 2.1b | **Verify tip_cid & chain_cid IPLD format** | ☐ Pass ☐ Fail |
| 2.2 | Export CAR | ☐ Pass ☐ Fail |
| 3.2 | **Delete volumes (nuclear)** | ☐ Completed |
| 4.1 | Restore from CAR | ☐ Pass ☐ Fail |
| **5.0** | **⚠️ /entities endpoint works** | ☐ Pass ☐ Fail |
| **5.0b** | **⚠️ recent_chain_head not null** | ☐ Pass ☐ Fail |
| 5.1 | Entity count matches | ☐ Pass ☐ Fail |
| 5.2 | Sample entity matches | ☐ Pass ☐ Fail |
| 5.3 | Version history intact | ☐ Pass ☐ Fail |
| 5.4 | Components retrievable | ☐ Pass ☐ Fail |
| 5.5 | Repo stats similar | ☐ Pass ☐ Fail |
| 6.1 | Create new version | ☐ Pass ☐ Fail |
| 6.2 | New version in history | ☐ Pass ☐ Fail |

### Success Criteria

**DR test passes if**:
- ✅ **All chain entries preserved** (validated by `/entities` endpoint working)
- ✅ **`recent_chain_head` not null** in restored index pointer
- ✅ All Phase 5 verifications match Phase 1
- ✅ All `.tip` files restored correctly
- ✅ All version histories intact
- ✅ All components retrievable
- ✅ New operations work (Phase 6)

**⚠️ CRITICAL**: Steps 5.0 and 5.0b are non-negotiable. If these fail, the DR system is broken.

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

### /entities Endpoint Returns Empty

**Symptom**: Step 5.0 fails - `/entities` returns `{"items": [], "total_count": 0}`

**Root Cause**: Chain entry blocks not included in CAR because `chain_cid` was plain string

**Debug**:
```bash
# Check if chain head CID exists in IPFS
curl -X POST http://localhost:5001/api/v0/files/read?arg=/arke/index-pointer | jq -r '.recent_chain_head["/"]'
# Copy the CID and try to fetch it:
curl -sf -X POST "http://localhost:5001/api/v0/dag/get?arg=<CHAIN_HEAD_CID>"
# If this times out or errors → block is missing!
```

**Fix**:
1. Check `build-snapshot.sh` around line 105
2. Ensure it uses IPLD link format for BOTH fields:
   ```bash
   # CORRECT:
   "tip_cid": {"/": "$tip_cid"},
   "chain_cid": {"/": "$chain_cid"}
   ```
3. Rebuild snapshot
4. Re-export CAR
5. Restore again

**Prevention**: Always verify tip_cid and chain_cid format in Step 2.1b before export

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
**Date**: 2025-10-12
**Duration**: 20 minutes (with clean environment setup)
**Overall Result**: ✓ PASS

---

**Actual Test Results** (2025-10-12):
- Complete data destruction confirmed (all volumes deleted)
- Clean environment: 3 fresh test entities (ENTITY_1, ENTITY_2, ENTITY_3)
- Snapshot build: v0 schema with direct entries array (no chunking)
- CAR export: 1.8 KB (6 blocks total)
- CAR import: 6 blocks restored
- MFS rebuild: automatic, no manual intervention
- Index pointer: `recent_chain_head` preserved correctly
- **Critical validation**: `/entities` endpoint working (returned all 3 entities)
- Verification: All .tip files created correctly
- Entity accessibility: Full end-to-end access confirmed
- System fully operational after restore

**Key Learnings**:
1. **CRITICAL**: Both `tip_cid` and `chain_cid` MUST be stored as IPLD links `{"/": $cid}` NOT plain strings
   - Plain string format: CAR export doesn't follow link → blocks not included
   - IPLD link format: CAR export follows link → all blocks included
   - Without tip manifests, entity version history is lost
   - Without chain entries, `/entities` endpoint returns empty after restore
2. dag-json codec is critical for IPLD link preservation
3. Direct array simpler than chunked linked list (removed 125 lines of complexity)
4. Timeout handling required for `ipfs dag export/import` (commands don't exit cleanly)
5. File validation (existence + size) more reliable than command exit codes
6. `.env` file inline comments break bash arithmetic parsing
7. Logging to stderr prevents stdout contamination in bash functions
8. MFS must be rebuilt from snapshot (not included in CAR)
9. Index pointer must be recreated with `recent_chain_head` from last snapshot entry
10. Single CAR file contains complete system state for full recovery

**Critical Implementation Detail**:
The `recent_chain_head` preservation was the primary goal of the 2025-10-12 test. Without it, the `/entities` endpoint (which walks the PI chain) would not function after disaster recovery. The fix required:
- Adding `chain_cid` field to snapshot entries
- Storing it as IPLD link format so CAR exporter includes the blocks
- Extracting it during restore to populate index pointer

**Architecture Simplification** (2025-10-12):
- Removed snapshot chunking (v3 → v0)
- Changed from linked list to direct array in snapshot object
- Simplified restore logic from ~40 lines to 3 lines
- Removed chunk walking from cleanup script
- Snapshots now faster (one dag put instead of N chunks)

---

**Notes**:
