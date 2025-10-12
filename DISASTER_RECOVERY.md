# Disaster Recovery Guide

Complete guide for backing up and restoring the Arke IPFS archive using CAR (Content Addressable aRchive) files.

## Overview

The DR strategy is built around **snapshot-based CAR exports** that capture:

1. **Snapshot Index**: A dag-json object mapping every PI → latest manifest CID
2. **Complete Graph**: All manifests (current + history via `prev` links) and components
3. **Self-Contained**: Single CAR file can restore the entire system on a fresh node

**Key Property**: You can restore from **only the CAR file** — no MFS backups, no S3, no IPNS required.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ MFS (Mutable File System)                                    │
│ /arke/index/01/K7/                                          │
│   ├── 01K75GZSKKSP2K6TP05JBFNV09.tip → bafyrei...          │
│   └── 01K75HQQXNTDG7BBP7PS9AWYAN.tip → bafyrei...          │
└─────────────────────────────────────────────────────────────┘
                            ↓
              ┌──────────────────────────┐
              │  Build Snapshot Index    │
              │  (dag-json object)       │
              └──────────────────────────┘
                            ↓
         {
           "schema": "arke/snapshot-index@v1",
           "seq": 1,
           "entries": [
             {"pi": "01K75...", "ver": 2, "tip": {"/": "baguqee..."}},
             ...
           ]
         }
                            ↓
              ┌──────────────────────────┐
              │  Export to CAR           │
              │  (single root = snapshot)│
              └──────────────────────────┘
                            ↓
                   arke-1-20251009.car
                   (portable archive)
                            ↓
              ┌──────────────────────────┐
              │  Import on Fresh Node    │
              └──────────────────────────┘
                            ↓
              ┌──────────────────────────┐
              │  Rebuild .tip files      │
              │  from snapshot           │
              └──────────────────────────┘
                            ↓
                   System Restored ✓
```

---

## Data Model

### Snapshot Index (dag-json) - v0

**Current Schema**: v0 uses direct array for simplicity

```json
{
  "schema": "arke/snapshot@v0",
  "seq": 42,
  "ts": "2025-10-12T03:05:24Z",
  "prev_snapshot": { "/": "baguqee...SNAP41" },  // link to previous snapshot (optional)
  "total_count": 3,
  "entries": [
    {
      "pi": "ENTITY_1",
      "ver": 2,
      "tip_cid": { "/": "bafyrei..." },       // IPLD link to manifest
      "chain_cid": { "/": "baguqee..." },     // IPLD link to chain entry
      "ts": "2025-10-12T03:05:17.769678Z"
    },
    {
      "pi": "ENTITY_2",
      "ver": 1,
      "tip_cid": { "/": "bafyrei..." },
      "chain_cid": { "/": "baguqee..." },
      "ts": "2025-10-12T03:05:18.123456Z"
    }
  ]
}
```

**Fields**:
- `schema`: Always `"arke/snapshot@v0"` for snapshot root
- `seq`: Monotonically increasing snapshot number (1, 2, 3, ...)
- `ts`: ISO 8601 timestamp of snapshot creation
- `prev_snapshot`: IPLD link to previous snapshot (for chaining; null for first)
- `total_count`: Total entities in entries array
- `entries`: Direct array of all entity snapshots (no chunking)
- **`tip_cid`**: ⚠️ **CRITICAL** - Must be IPLD link format `{"/": "cid"}` for CAR export
- **`chain_cid`**: ⚠️ **CRITICAL** - Must be IPLD link format `{"/": "cid"}` for CAR export

**Why dag-json and IPLD Links?**

**Critical**: The snapshot MUST be stored as `dag-json` (not `dag-cbor`) to ensure IPLD links are properly preserved for CAR export.

Using `{"/": "cid"}` format with dag-json ensures CAR exporters follow links and include:
- All tip manifests (via `tip_cid` field)
- All historical manifests (via `prev` links in manifests)
- All components (metadata, files, images)
- **All chain entries** (via `chain_cid` field) ⚠️ **CRITICAL FOR /entities ENDPOINT**

**⚠️ IPLD Link Requirement**:
```bash
# WRONG - plain string (blocks NOT included in CAR):
"tip_cid": "bafyrei..."
"chain_cid": "baguqee..."

# CORRECT - IPLD link (blocks included in CAR):
"tip_cid": {"/": "bafyrei..."}
"chain_cid": {"/": "baguqee..."}
```

The CAR exporter ONLY follows IPLD links in `{"/": "cid"}` format. Plain string CIDs are stored but not followed, causing missing blocks after restore.

---

## Scripts

### 1. Build Snapshot (`scripts/build-snapshot.sh`)

**Purpose**: Scan MFS and chain, create snapshot with preserved chain head

**Usage**:
```bash
./scripts/build-snapshot.sh
```

**What it does**:
1. Reads current index pointer from `/arke/index-pointer`
2. Walks the PI chain from `recent_chain_head` to collect all entities
3. For each entity: reads `.tip` file from MFS, fetches manifest for version number
4. **CRITICAL**: Stores both `tip_cid` and `chain_cid` as IPLD links `{"/": $cid}` (not plain strings)
5. Creates snapshot object with direct entries array (no chunking)
6. Stores snapshot as dag-json using HTTP API
7. Saves metadata to `snapshots/latest.json`

**Critical Implementation Notes**:
- Must use `dag-json` codec to preserve IPLD link semantics
- **Must store `tip_cid` and `chain_cid` as IPLD links** so CAR export includes all blocks
- Chain entries are required for `/entities` endpoint to work after restore
- Tip manifests are required for entity version history
- Without IPLD link format, blocks are not included in CAR file

**Output**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot Build Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CID:      baguqeeraz...
Sequence: 1
Entities: 4
Time:     2025-10-09T23:00:00Z
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Note: dag-json CIDs start with `baguqee...` prefix.

**Metadata saved**:
- `snapshots/snapshot-1.json`
- `snapshots/latest.json` (symlink to latest)

---

### 2. Export CAR (`scripts/export-car.sh`)

**Purpose**: Export snapshot to portable CAR file with all linked blocks

**Usage**:
```bash
./scripts/export-car.sh
```

**What it does**:
1. Reads latest snapshot CID from `snapshots/latest.json`
2. Exports using `ipfs dag export <SNAPSHOT_CID>` with 60s timeout
3. Follows all IPLD links to include: manifests (via `tip_cid`), components, **and chain entries (via `chain_cid`)**
4. Validates CAR file was created and has content
5. Saves to `backups/arke-<seq>-<timestamp>.car`
6. Creates metadata file with snapshot CID for restore

**Timeout Handling**:
- Uses `timeout 60s` wrapper as `ipfs dag export` may not exit cleanly
- Validates file existence and size instead of relying on command exit code
- Typical small archives export in < 5 seconds

**Output**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAR Export Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot:  baguqeeraz...
Sequence:  2
File:      arke-2-20251012-030530.car
Size:      1.8 KB
Location:  ./backups/arke-2-20251012-030530.car
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**What's included in CAR**:
- Snapshot root with direct entries array
- All entity manifests (current versions via `tip_cid` links)
- All historical manifests (via `prev` links in manifests)
- All components (metadata, images, files)
- **All chain entries** (via `chain_cid` IPLD links) ← Required for `/entities` endpoint

---

### 3. Restore from CAR (`scripts/restore-from-car.sh`)

**⚠️ Important**: This script has been thoroughly tested with the "nuclear option" (complete data destruction and restoration from CAR alone). All logging outputs to stderr to avoid contaminating function return values.

**Purpose**: Restore complete system on fresh node from CAR file alone

**Usage**:
```bash
./scripts/restore-from-car.sh <car-file> [snapshot-cid]
```

**Examples**:
```bash
# Automatic (reads snapshot CID from metadata)
./scripts/restore-from-car.sh backups/arke-2-20251012-030530.car

# Manual (provide snapshot CID explicitly)
./scripts/restore-from-car.sh backups/arke-2-20251012-030530.car baguqeeraz...
```

**What it does**:
1. Imports CAR file with `ipfs dag import --stats` (60s timeout)
2. Fetches snapshot root and reads entries array directly (no chunk walking)
3. For each entry: creates MFS directory and writes `.tip` file
4. **Rebuilds index pointer** with preserved `recent_chain_head`
5. Verifies all unique PIs have corresponding `.tip` files

**Critical: Chain Head Preservation**:
- Extracts `chain_cid` from last snapshot entry (newest operation)
- Creates `/arke/index-pointer` with `recent_chain_head` set to this CID
- This enables `/entities` endpoint to work immediately after restore
- Without this, entity listing would return empty results

**Timeout Handling**:
- Uses `timeout 60s` for import as command may not exit cleanly
- Parses import stats from output logs instead of relying on exit code

**Output**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Restoration Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot:   baguqeeraz... (seq 2)
Entities:   3
MFS:        /arke/index
Status:     ✓ All verified
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

System restored from CAR file! Ready to serve requests.
```

**What gets restored**:
- All `.tip` files in MFS with proper sharding
- Index pointer with `recent_chain_head` (enables `/entities` endpoint)
- All manifests (current + historical versions)
- All components (metadata, images, files)
- All chain entries (entity operation history)

---

### 4. Verify Entity (`scripts/verify-entity.sh`)

**Purpose**: Verify an entity is fully accessible (useful after restore)

**Usage**:
```bash
./scripts/verify-entity.sh <PI>
```

**Example**:
```bash
./scripts/verify-entity.sh 01K75ZA2RHKTJ3GDND0ZK46M4G
```

**What it does**:
1. Reads .tip file from MFS
2. Fetches manifest from tip CID
3. Retrieves metadata component
4. Verifies image/file components are accessible

**Output**:
```
Verifying entity: 01K75ZA2RHKTJ3GDND0ZK46M4G

1. Reading .tip file from MFS...
   Tip CID: baguqeerayq...

2. Fetching manifest...
{manifest JSON...}

3. Accessing metadata component...
{metadata JSON...}

4. Verifying image component...
{block stats...}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Entity fully accessible!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Operational Procedures

### Backup Schedule

**Recommended**:
- **Hourly**: Build snapshots (cheap; just a small dag-json object)
- **Daily**: Export CAR files
- **Weekly**: Verify CAR integrity on staging node
- **Monthly**: Test full restore procedure

**Automation** (cron example):
```bash
# Build snapshot every hour
0 * * * * cd /path/to/ipfs-server && ./scripts/build-snapshot.sh >> /var/log/arke-snapshots.log 2>&1

# Export CAR daily at 2 AM
0 2 * * * cd /path/to/ipfs-server && ./scripts/export-car.sh >> /var/log/arke-cars.log 2>&1
```

---

### Full Backup Procedure

1. **Build Snapshot**:
   ```bash
   ./scripts/build-snapshot.sh
   ```

2. **Export CAR**:
   ```bash
   ./scripts/export-car.sh
   ```

3. **Copy Offsite**:
   ```bash
   # To S3
   aws s3 cp backups/arke-*.car s3://arke-backups/

   # To another server
   scp backups/arke-*.car backup-server:/backups/
   ```

4. **Verify**:
   ```bash
   # Check file size
   ls -lh backups/arke-*.car

   # Test import on staging (don't rebuild tips)
   docker exec ipfs-node ipfs dag import --stats /path/to/car
   ```

---

### Disaster Recovery Procedure

**Scenario**: Production IPFS node lost; need to restore on fresh instance.

1. **Provision new EC2 instance** and install Docker

2. **Clone repository and start node**:
   ```bash
   git clone https://github.com/Arke-Institute/ipfs-server.git
   cd ipfs-server
   docker compose -f docker-compose.prod.yml up -d
   ```

3. **Wait for node to initialize** (~30 seconds):
   ```bash
   docker compose logs -f
   # Wait for "Daemon is ready"
   ```

4. **Copy CAR file to server**:
   ```bash
   scp backups/arke-latest.car ec2-user@new-instance:/home/ec2-user/ipfs-server/
   ```

5. **Restore from CAR**:
   ```bash
   ./scripts/restore-from-car.sh arke-latest.car
   ```

6. **Verify restoration**:
   ```bash
   # Check tips
   curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index

   # Verify specific entity
   ./scripts/verify-entity.sh 01K75GZSKKSP2K6TP05JBFNV09
   ```

7. **Resume operations** ✓

**RTO (Recovery Time Objective)**: ~15 minutes for typical archive sizes

**RPO (Recovery Point Objective)**: Last snapshot (hourly = max 1 hour data loss)

---

## Retention Policy

### Snapshot Objects

**Keep**:
- Last 24 snapshots (1 day if hourly)
- Weekly snapshots for 1 month
- Monthly snapshots indefinitely

**Why**: Snapshots are tiny (few KB); cheap to keep for history.

### CAR Files

**Keep**:
- Last 7 daily CARs (1 week)
- Weekly CARs for 1 month
- Monthly CARs for 1 year
- Yearly CARs indefinitely

**Cleanup**:
```bash
# Delete CARs older than 30 days
find backups/ -name "arke-*.car" -mtime +30 -delete
```

### IPFS Blocks (Manifests)

**Keep all or apply retention per entity**:
- If you want **full history forever**: pin all manifests, never unpin
- If you want **last N versions**: unpin manifests older than N

**Key principle**: Don't GC blocks included in the latest exported CAR that you rely on for DR.

---

## Monitoring & Alerts

**Metrics to track**:
- Snapshot build success/failure
- CAR export success/failure
- CAR file size trend (sudden spike = investigate)
- Time since last successful backup
- Offsite copy age

**Alert conditions**:
- Snapshot build failed 3 times in a row
- CAR export failed
- No offsite backup in 48 hours
- CAR file size doubled (possible data corruption)

**Healthcheck**:
```bash
#!/bin/bash
# Check if latest snapshot is recent
LATEST=$(jq -r '.ts' snapshots/latest.json)
AGE_SECONDS=$(( $(date +%s) - $(date -d "$LATEST" +%s) ))
MAX_AGE=7200  # 2 hours

if [[ $AGE_SECONDS -gt $MAX_AGE ]]; then
  echo "WARNING: Latest snapshot is $(($AGE_SECONDS / 3600)) hours old"
  exit 1
fi
```

---

## Testing

### Dry Run Test (Non-Destructive)

Test restore on a **separate staging node**:

1. Start staging node:
   ```bash
   docker compose -f docker-compose.yml up -d
   ```

2. Restore CAR:
   ```bash
   ./scripts/restore-from-car.sh backups/arke-1-latest.car
   ```

3. Verify:
   ```bash
   # Compare tip counts
   curl -X POST http://localhost:5001/api/v0/files/stat?arg=/arke/index | jq .
   ```

4. Clean up:
   ```bash
   docker compose down -v
   ```

---

### Nuclear Test (Destructive)

**Warning**: This deletes all data! Only run in controlled environment.

See `DR_TEST.md` for step-by-step nuclear test procedure.

---

## Troubleshooting

### Snapshot Build Fails

**Symptom**: `build-snapshot.sh` exits with error

**Possible causes**:
- IPFS node not running
- `.tip` file unreadable
- Manifest CID invalid
- `.env` file has inline comments (breaks bash parsing)

**Fix**:
```bash
# Check node
docker compose ps

# Check logs
docker compose logs ipfs

# Verify MFS structure
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index

# Check .env format (comments must be on separate lines)
cat .env
# WRONG: CHUNK_SIZE=10000  # comment
# CORRECT:
# # comment
# CHUNK_SIZE=10000
```

---

### CAR Export Hangs or Times Out

**Symptom**: `export-car.sh` hangs for 60+ seconds or produces empty file

**Possible causes**:
- `ipfs dag export` command not exiting cleanly
- Snapshot CID not pinned
- Disk full
- IPFS daemon unresponsive

**Fix**:
```bash
# Verify snapshot exists
SNAP_CID=$(jq -r '.cid' snapshots/latest.json)
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID"

# Check disk space
df -h

# Try manual export with timeout
timeout 60s docker exec ipfs-node ipfs dag export "$SNAP_CID" > test.car || true
ls -lh test.car  # Check if file was created despite timeout

# Check IPFS daemon health
docker compose logs ipfs --tail 50
curl -X POST http://localhost:5001/api/v0/version
```

**Note**: The timeout is expected behavior. The script validates file creation and size instead of relying on command exit code.

---

### /entities Endpoint Returns Empty After Restore

**Symptom**: After CAR restore, `/entities` endpoint returns `[]` even though `.tip` files exist

**Root Cause**: Chain entry CIDs were stored as plain strings, not IPLD links

**Debug**:
```bash
# Check if recent_chain_head exists in index pointer
curl -X POST http://localhost:5001/api/v0/files/read?arg=/arke/index-pointer | jq .

# Try to fetch chain head CID
CHAIN_HEAD="baguqee..."  # from index pointer
curl -sf -X POST "http://localhost:5001/api/v0/dag/get?arg=$CHAIN_HEAD"
# If this times out or returns error: chain entry block is missing!
```

**Fix**:
1. Check `build-snapshot.sh` line ~105 - must use IPLD link format:
   ```bash
   # WRONG:
   "tip_cid": "$tip_cid",
   "chain_cid": "$chain_cid"

   # CORRECT:
   "tip_cid": {"/": "$tip_cid"},
   "chain_cid": {"/": "$chain_cid"}
   ```
2. Rebuild snapshot with corrected script
3. Export new CAR
4. Restore again - all blocks will now be included

---

### Restore Incomplete

**Symptom**: Some `.tip` files missing after restore

**Possible causes**:
- CAR file truncated
- Import timed out before completion
- Snapshot entries array malformed

**Fix**:
```bash
# Re-import with stats
timeout 60s docker exec ipfs-node ipfs dag import --stats /path/to/car

# Check snapshot object
SNAP_CID=$(jq -r '.cid' snapshots/latest.json)
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq .

# Verify entries array
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq '.entries | length'
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq '.entries[] | {pi, ver}'
```

---

## Security Considerations

1. **CAR File Encryption**: Consider encrypting CARs before offsite storage
   ```bash
   gpg -c backups/arke-1-latest.car
   ```

2. **Access Control**: Restrict access to backup files (sensitive archive data)

3. **Integrity Checks**: SHA256 checksums for all CAR files
   ```bash
   sha256sum backups/arke-*.car > backups/checksums.txt
   ```

4. **Offsite Security**: Use encrypted channels (HTTPS, SCP) for transfers

---

## Next Steps

1. **Set up automated backups** (cron jobs)
2. **Configure offsite storage** (S3, rsync, etc.)
3. **Run your first DR test** (see `DR_TEST.md`)
4. **Document your RTO/RPO** requirements
5. **Create runbook** for your team

---

## Resources

- [IPFS CAR Specification](https://ipld.io/specs/transport/car/)
- [Kubo dag export/import docs](https://docs.ipfs.tech/reference/kubo/cli/#ipfs-dag-export)
- [Arke API Specification](./API_WALKTHROUGH.md)
- [DR Test Procedure](./DR_TEST.md)

---

**Last Updated**: 2025-10-12
**Maintained By**: Arke Institute

**Tested**: Nuclear test completed successfully on 2025-10-12 with the following critical verifications:
- Complete data destruction and restoration from CAR file
- Chain head preservation through export/import cycle
- `/entities` endpoint functionality after restore (validates chain entries included)
- IPLD link format requirement for `chain_cid` field confirmed
- Timeout handling for `ipfs dag export/import` commands validated
