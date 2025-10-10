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

### Snapshot Index (dag-json)

```json
{
  "schema": "arke/snapshot-index@v1",
  "seq": 42,
  "ts": "2025-10-09T23:00:00Z",
  "prev": { "/": "baguqee...SNAP41" },  // link to previous snapshot (optional)
  "entries": [
    {
      "pi": "01K75GZSKKSP2K6TP05JBFNV09",
      "ver": 2,
      "tip": { "/": "baguqeerayq..." }
    },
    {
      "pi": "01K75HQQXNTDG7BBP7PS9AWYAN",
      "ver": 1,
      "tip": { "/": "baguqeerayq..." }
    }
  ]
}
```

**Fields**:
- `schema`: Always `"arke/snapshot-index@v1"`
- `seq`: Monotonically increasing snapshot number (1, 2, 3, ...)
- `ts`: ISO 8601 timestamp of snapshot creation
- `prev`: IPLD link to previous snapshot (for chaining; null for first)
- `entries`: Array of all entities with their current tip

**Why dag-json and IPLD Links?**

**Critical**: The snapshot MUST be stored as `dag-json` (not `dag-cbor`) to ensure IPLD links are properly preserved for CAR export.

Using `{"/": "cid"}` format with dag-json ensures CAR exporters follow links and include:
- All tip manifests
- All historical manifests (via `prev` links in manifests)
- All components (metadata, files, images)

**Important Implementation Detail**: Use the Kubo CLI (`ipfs dag put --store-codec=dag-json`) instead of the HTTP API, as the HTTP API's dag-cbor encoding doesn't preserve IPLD link semantics correctly.

---

## Scripts

### 1. Build Snapshot (`scripts/build-snapshot.sh`)

**Purpose**: Scan MFS, create snapshot index

**Usage**:
```bash
./scripts/build-snapshot.sh
```

**What it does**:
1. Recursively walks `/arke/index/` to find all `.tip` files
2. Reads each tip to get manifest CID
3. Fetches manifest to get version number
4. Builds snapshot object with all entries
5. Stores as dag-json using Kubo CLI (`ipfs dag put --store-codec=dag-json`)
6. Saves metadata to `snapshots/latest.json`

**Critical**: Must use `dag-json` codec to preserve IPLD link semantics for CAR export.

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

**Purpose**: Export snapshot to portable CAR file

**Usage**:
```bash
./scripts/export-car.sh
```

**What it does**:
1. Reads latest snapshot CID from `snapshots/latest.json`
2. Exports using `ipfs dag export <SNAPSHOT_CID>`
3. Saves to `backups/arke-<seq>-<timestamp>.car`
4. Creates metadata file `backups/arke-<seq>-<timestamp>.json`

**Output**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAR Export Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot:  baguqeeraz...
Sequence:  1
File:      arke-1-20251009-123456.car
Size:      1.2 MB
Location:  ./backups/arke-1-20251009-123456.car
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

### 3. Restore from CAR (`scripts/restore-from-car.sh`)

**⚠️ Important**: This script has been thoroughly tested with the "nuclear option" (complete data destruction and restoration from CAR alone). All logging outputs to stderr to avoid contaminating function return values.

**Purpose**: Restore on fresh node from CAR file alone

**Usage**:
```bash
./scripts/restore-from-car.sh <car-file> [snapshot-cid]
```

**Examples**:
```bash
# Automatic (reads snapshot CID from metadata)
./scripts/restore-from-car.sh backups/arke-1-20251009-123456.car

# Manual (provide snapshot CID explicitly)
./scripts/restore-from-car.sh backups/arke-1-20251009-123456.car bafyreiabc123...
```

**What it does**:
1. Imports CAR file (`ipfs dag import`)
2. Fetches snapshot object
3. For each entry in snapshot:
   - Creates MFS directory with proper sharding (`/arke/index/01/K7/`)
   - Writes `.tip` file pointing to manifest CID
4. Verifies all tips match snapshot

**Output**:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Restoration Complete
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Snapshot:   baguqeeraz... (seq 1)
Entities:   4
MFS:        /arke/index
Status:     ✓ All verified
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

System restored from CAR file! Ready to serve requests.
```

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

**Fix**:
```bash
# Check node
docker compose ps

# Check logs
docker compose logs ipfs

# Verify MFS structure
curl -X POST http://localhost:5001/api/v0/files/ls?arg=/arke/index
```

---

### CAR Export Fails

**Symptom**: `export-car.sh` produces empty or corrupt file

**Possible causes**:
- Snapshot CID not pinned
- Disk full
- IPFS daemon crashed

**Fix**:
```bash
# Verify snapshot exists
SNAP_CID=$(jq -r '.cid' snapshots/latest.json)
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID"

# Check disk space
df -h

# Try manual export
docker exec ipfs-node ipfs dag export $SNAP_CID > test.car
```

---

### Restore Incomplete

**Symptom**: Some `.tip` files missing after restore

**Possible causes**:
- CAR file truncated
- Import failed partway
- Snapshot object incomplete

**Fix**:
```bash
# Re-import with stats
docker exec ipfs-node ipfs dag import --stats /path/to/car

# Check snapshot object
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq '.entries | length'

# Manually verify each entry
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=$SNAP_CID" | jq -r '.entries[].tip["/"]' | while read cid; do
  curl -sf -X POST "http://localhost:5001/api/v0/dag/get?arg=$cid" || echo "Missing: $cid"
done
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

**Last Updated**: 2025-10-10
**Maintained By**: Arke Institute

**Tested**: Nuclear test completed successfully on 2025-10-10 - complete data destruction and restoration from CAR verified.
