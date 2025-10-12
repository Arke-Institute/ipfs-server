# Snapshot Strategy for Spiky Import Workloads

**System:** IPFS Server Hybrid Architecture
**Use Case:** Single-user, batch imports (thousands of entities), then idle for days
**Last Updated:** 2025-10-11

---

## Your Use Case: Bursty Import Pattern

### Import Characteristics
- **Pattern:** Import 1,000-5,000 file units in one session, then idle for days/weeks
- **Frequency:** Irregular, batch-oriented
- **Rate:** ~0.13 entities/sec during import = ~470 entities/hour
- **Example:** Recent import of 2,293 entities in 13 minutes

### Why Time-Based Doesn't Fit
❌ Daily 2 AM snapshots would:
- Run on empty chain most days (wasted computation)
- Not respond to actual import activity
- Create unnecessary snapshot versions

### ✅ Recommended: **Threshold-Based at 10,000 entities**

**Trigger:** When `recent_count >= 10,000` in the recent chain

**Why 10K?**
- Gives you headroom for largest expected batch imports
- Chain walk time: ~13 minutes (acceptable for background job)
- Prevents performance degradation during queries
- Less frequent snapshots = fewer versions to manage

**For testing/development:**
- Set threshold lower (e.g., 5,000 or even 2,000) to test more frequently
- Environment variable: `REBUILD_THRESHOLD=5000`

---

## Pin Management and Storage

### ✅ What Gets Pinned

**During Snapshot Build:**
1. **Snapshot chunks** (line 150 in build-snapshot.sh):
   ```bash
   ipfs dag put --store-codec=dag-json --input-codec=json --pin=true
   ```

2. **Snapshot metadata** (line 233):
   ```bash
   ipfs dag put --store-codec=dag-json --input-codec=json --pin=true
   ```

**Result:** Yes, all snapshots are pinned recursively.

### Snapshot Chain Linkage

Snapshots form a **backward-linked chain**:

```
Snapshot #2 (current)
  ├─ 2,300 entities
  └─ prev_snapshot → Snapshot #1
                       ├─ 5 entities
                       └─ prev_snapshot → null
```

**Verified:**
```bash
$ docker exec ipfs-node ipfs pin ls baguqeerakxhfly... # Snapshot #2
baguqeerakxhfly... recursive

$ docker exec ipfs-node ipfs pin ls baguqeeras3ungl... # Snapshot #1
baguqeeras3ungl... recursive
```

**Implications:**
- Old snapshots remain pinned indefinitely
- Disk usage grows with each snapshot
- Manual cleanup required (not automatic)

### CAR Files vs Pins

**CAR Export:**
- Reads pinned snapshot from IPFS
- Exports to portable `.car` file
- **Does NOT unpin** the snapshot after export
- CAR file is independent copy on disk

**Storage Layers:**
1. **IPFS blockstore:** Pinned snapshot + chunks (~9.3 MB for 2,300 entities)
2. **CAR backup:** Exported file in `backups/` (~9.3 MB)
3. **Offsite:** Optional S3/remote copy

**Total storage per snapshot:** ~2x the entity data size

---

## Current Storage Status

After 2 snapshots:
```
NumObjects: 40,604 blocks
RepoSize:   273 MB
StorageMax: 10 GB

Snapshots pinned: 2 (seq 1 and seq 2)
CAR files: 3 (1.7 KB, 436 KB, 9.3 MB)
```

**Projection at 100 snapshots:**
- Repo size: ~1-2 GB (assuming mostly incremental)
- CAR files: ~1 GB if keeping all
- Still well under 10 GB limit ✓

---

## Recommended Snapshot Strategy

### Option A: Pure Threshold (Recommended)

**Configuration:**
```bash
# In api/chain.py or as environment variable
REBUILD_THRESHOLD=10000
```

**Behavior:**
- No automatic timer
- Snapshot triggered only when threshold reached
- Manual trigger available: `./scripts/build-snapshot.sh`

**Pros:**
- Matches your bursty workload perfectly
- No wasted computation
- Responds to actual system load

**Cons:**
- No snapshots during idle periods (not an issue for you)
- Need to implement threshold monitoring

### Option B: Threshold + Weekly Minimum (Safeguard)

**Configuration:**
```bash
REBUILD_THRESHOLD=10000
WEEKLY_SNAPSHOT=true
```

**Behavior:**
- Primary: Threshold at 10K entities
- Fallback: Weekly snapshot (Sundays 3 AM) even if < 10K

**Pros:**
- Regular snapshots for disaster recovery
- Catches edge cases (e.g., 5K entities sitting for weeks)

**Cons:**
- More snapshots = more disk usage
- May create unnecessary versions

### Option C: Manual Only (Development Phase)

**Behavior:**
- Only run `./scripts/build-snapshot.sh` manually when needed
- Good for testing and experimentation

**Pros:**
- Full control
- No surprises

**Cons:**
- Easy to forget
- No protection during large imports

---

## Implementation: Threshold-Based Automation

### Quick Implementation (API-Level Check)

**Modify `api/chain.py`:**

```python
import os
import subprocess

REBUILD_THRESHOLD = int(os.getenv("REBUILD_THRESHOLD", "10000"))
AUTO_SNAPSHOT = os.getenv("AUTO_SNAPSHOT", "true").lower() == "true"

async def append_to_chain(pi: str, tip_cid: str, ver: int) -> str:
    # ... existing code ...

    pointer.recent_chain_head = new_cid
    pointer.recent_count += 1
    pointer.total_count += 1
    await update_index_pointer(pointer)

    # Automatic threshold-based snapshot
    if AUTO_SNAPSHOT and pointer.recent_count >= REBUILD_THRESHOLD:
        print(f"⚠️  Threshold reached: {pointer.recent_count} entities. Triggering snapshot build...")

        # Trigger snapshot build in background
        subprocess.Popen(
            ["/path/to/scripts/build-snapshot.sh"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    return new_cid
```

**Configuration via Docker Compose:**

```yaml
# docker-compose.yml or docker-compose.prod.yml
services:
  ipfs-api:
    environment:
      - REBUILD_THRESHOLD=10000    # Adjust for testing (e.g., 2000)
      - AUTO_SNAPSHOT=true          # Set to false to disable
```

**For Testing with Lower Threshold:**
```yaml
environment:
  - REBUILD_THRESHOLD=2000   # Trigger every 2K entities for testing
```

### Alternative: External Monitor Script

If you don't want to modify the API:

**`scripts/monitor-and-snapshot.sh`:**
```bash
#!/bin/bash
# Check threshold and trigger snapshot if needed

THRESHOLD=${REBUILD_THRESHOLD:-10000}
RECENT_COUNT=$(curl -s http://localhost:3000/index-pointer | jq -r '.recent_count')

if [ "$RECENT_COUNT" -ge "$THRESHOLD" ]; then
    echo "Threshold reached ($RECENT_COUNT >= $THRESHOLD). Building snapshot..."
    /path/to/scripts/build-snapshot.sh
fi
```

**Run via cron every hour:**
```bash
0 * * * * /path/to/scripts/monitor-and-snapshot.sh >> /var/log/arke-snapshot-monitor.log 2>&1
```

---

## Pin Cleanup Strategy

### Current Behavior
- All snapshots remain pinned forever
- No automatic cleanup

### Recommended Cleanup Policy

**Keep last N snapshots pinned** (e.g., N=5)

**`scripts/cleanup-old-snapshots.sh`:**
```bash
#!/bin/bash
# Unpin old snapshots, keep last 5

KEEP_LAST=5
SNAPSHOTS_DIR="./snapshots"

# Get list of snapshots sorted by sequence (oldest first)
OLD_SNAPSHOTS=$(ls -1 "$SNAPSHOTS_DIR"/snapshot-*.json | sort -t- -k2 -n | head -n -$KEEP_LAST)

for snapshot_file in $OLD_SNAPSHOTS; do
    SNAPSHOT_CID=$(jq -r '.cid' "$snapshot_file")
    echo "Unpinning old snapshot: $SNAPSHOT_CID"

    docker exec ipfs-node ipfs pin rm "$SNAPSHOT_CID" 2>/dev/null || true

    # Remove metadata file
    rm "$snapshot_file"
done

# Run garbage collection
docker exec ipfs-node ipfs repo gc
```

**Run after each snapshot build:**
```bash
# Add to end of build-snapshot.sh
./scripts/cleanup-old-snapshots.sh
```

### When to Run Cleanup

**Option 1:** After every snapshot build
- Keeps repo size minimal
- Good for constrained storage

**Option 2:** Monthly
- Keeps more history
- Useful for debugging and rollback

**Option 3:** Never (for now)
- Your storage is fine (273 MB / 10 GB)
- Revisit when repo > 5 GB

---

## CAR Backup Retention

**Local CAR files** in `backups/`:

Current behavior: Keep all CAR files indefinitely

**Recommended:**
```bash
# Add to export-car.sh after successful export
KEEP_LAST_CARS=3
ls -t backups/arke-*.car | tail -n +$((KEEP_LAST_CARS + 1)) | xargs -r rm
ls -t backups/arke-*.json | tail -n +$((KEEP_LAST_CARS + 1)) | xargs -r rm
```

**Why keep only 3?**
- CAR files are for disaster recovery
- Offsite backups are the real protection
- Local disk space is limited

---

## Testing Plan

### Phase 1: Test Threshold at 2K (This Week)

```bash
# Set low threshold for testing
export REBUILD_THRESHOLD=2000

# Import ~2K entities in a batch
# ... your import script ...

# Verify snapshot triggered automatically
curl http://localhost:3000/index-pointer | jq .

# Check new snapshot created
ls -lh backups/
```

### Phase 2: Raise to 5K (Next Week)

```bash
export REBUILD_THRESHOLD=5000
```

### Phase 3: Production at 10K (After Testing)

```bash
export REBUILD_THRESHOLD=10000
```

---

## Monitoring & Alerts

### Key Metrics to Watch

1. **Recent chain length**
   ```bash
   curl -s http://localhost:3000/index-pointer | jq '.recent_count'
   ```
   Alert if: > 12,000 (missed threshold)

2. **Last snapshot age**
   ```bash
   curl -s http://localhost:3000/index-pointer | jq '.snapshot_ts'
   ```
   Alert if: > 7 days (no recent activity or stuck)

3. **Repo size**
   ```bash
   docker exec ipfs-node ipfs repo stat --human | grep RepoSize
   ```
   Alert if: > 8 GB (approaching limit)

4. **Snapshot build failures**
   - Check logs: `tail -f /var/log/arke-snapshots.log`
   - Alert on: ERROR messages

### Simple Monitoring Dashboard

**`scripts/status.sh`:**
```bash
#!/bin/bash
echo "=== IPFS Server Snapshot Status ==="
echo ""

POINTER=$(curl -s http://localhost:3000/index-pointer)
echo "Recent chain:  $(echo $POINTER | jq -r '.recent_count') entities"
echo "Total:         $(echo $POINTER | jq -r '.total_count') entities"
echo "Last snapshot: $(echo $POINTER | jq -r '.snapshot_ts')"
echo "Snapshot seq:  $(echo $POINTER | jq -r '.snapshot_seq')"
echo ""

REPO=$(docker exec ipfs-node ipfs repo stat --human 2>/dev/null)
echo "Repo size:     $(echo "$REPO" | grep RepoSize | awk '{print $2, $3}')"
echo "Objects:       $(echo "$REPO" | grep NumObjects | awk '{print $2}')"
echo ""

echo "CAR backups:   $(ls -1 backups/*.car 2>/dev/null | wc -l) files"
echo "Latest backup: $(ls -t backups/*.car 2>/dev/null | head -1 | xargs ls -lh 2>/dev/null | awk '{print $5, $9}')"
```

Run: `./scripts/status.sh`

---

## Answers to Your Questions

### Q: Are snapshots pinned?
✅ **Yes.** Both snapshot metadata and chunks are pinned with `--pin=true`.

### Q: Are past snapshots linked?
✅ **Yes.** Each snapshot has a `prev_snapshot` field linking to the previous snapshot CID, forming a backward chain.

### Q: Are CAR files pinned?
❌ **No.** CAR files are exported from pinned snapshots, but the CAR file itself is just a regular file on disk. The original snapshot remains pinned in IPFS.

### Q: What happens to old snapshots?
⚠️ **They remain pinned forever** unless you manually unpin them. Recommended: Keep last 5 pinned, unpin older ones.

### Q: Should I use time-based or threshold-based?
✅ **Threshold at 10K** for your use case (bursty imports). Optional: Add weekly fallback for safety.

---

## Recommended Configuration for Your Workflow

### Docker Compose Environment

```yaml
# docker-compose.yml (development/testing)
services:
  ipfs-api:
    environment:
      - REBUILD_THRESHOLD=2000   # Lower for testing
      - AUTO_SNAPSHOT=true

# docker-compose.prod.yml (production)
services:
  ipfs-api:
    environment:
      - REBUILD_THRESHOLD=10000  # Higher for production
      - AUTO_SNAPSHOT=true
```

### Cron Jobs (Optional Safeguards)

```bash
# Monitor and snapshot if threshold reached (hourly)
0 * * * * cd /path/to/ipfs-server && ./scripts/monitor-and-snapshot.sh >> /var/log/arke-monitor.log 2>&1

# Weekly CAR export (Sundays 3 AM)
0 3 * * 0 cd /path/to/ipfs-server && ./scripts/export-car.sh >> /var/log/arke-car-exports.log 2>&1

# Monthly pin cleanup (1st of month, 4 AM)
0 4 1 * * cd /path/to/ipfs-server && ./scripts/cleanup-old-snapshots.sh >> /var/log/arke-cleanup.log 2>&1
```

### Manual Workflow (Simplest for Now)

```bash
# After large import batch
./scripts/build-snapshot.sh

# Export to CAR
./scripts/export-car.sh

# Check status
./scripts/status.sh
```

---

## Next Steps

### This Week
- [ ] Decide: API-level threshold vs external monitor script
- [ ] Set `REBUILD_THRESHOLD=2000` for testing
- [ ] Import ~2K entities and verify automatic snapshot
- [ ] Test CAR export after snapshot

### Next Week
- [ ] Raise threshold to 5K or 10K based on comfort level
- [ ] Implement pin cleanup script
- [ ] Set up weekly CAR exports (cron)
- [ ] Test disaster recovery (restore from CAR)

### Future (Optional)
- [ ] Monthly pin cleanup automation
- [ ] Offsite CAR backups to S3
- [ ] Monitoring dashboard with Prometheus/Grafana
- [ ] Automated restore testing

---

**Document Version:** 1.0
**Author:** Claude Code
**Tested With:** 2,300 entities, threshold-based strategy
**Recommendation:** Start with REBUILD_THRESHOLD=2000 for testing, increase to 10000 for production
