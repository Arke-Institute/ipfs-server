# Snapshot Automation Guide

**Status:** Production Recommendation
**Last Updated:** 2025-10-11
**System:** IPFS Server Hybrid Architecture (Snapshot + Recent Chain)

---

## Executive Summary

The IPFS Server backend successfully built and exported a snapshot containing **2,300 entities** (2,293 from recent import + 5 test entities). The snapshot system is working correctly, but **automatic snapshotting is not currently implemented**. This document provides recommendations for production snapshot automation.

---

## Current State Analysis

### Import Statistics (2025-10-11)
- **Entities Created:** 2,293 new entities
  - 1 institution
  - 1 collection
  - 4 series
  - 100 file units
  - 2,187 digital objects
- **Import Duration:** 13.2 minutes (791 seconds)
- **Processing Rate:** 0.13 entities/second
- **Data Size:** ~1.28 GB hashed content

### Snapshot Build Results
- **Snapshot Sequence:** 2
- **Total Entities:** 2,300
- **Chunk Count:** 1 (entities fit in single 10K chunk)
- **Build Time:** ~3 minutes for 2,295 chain entries
- **CAR Export:** 9.3 MB (9,754,019 bytes)
- **CID:** `baguqeerakxhfly2rucfd5ssrhpw25kxmzvoizoe4h5tsqu3zsoqral5g67xq`

### System Verification ✅
- All 2,300 entities accessible via pagination
- Deep pagination tested (offset 0, 1000, 2000, 2295)
- CAR export successful with valid magic bytes
- Index pointer correctly reset (recent_count: 0)
- Snapshot queries working across entire range

---

## Why Automatic Snapshotting is Critical

### 1. **Performance Degradation**
Without regular snapshots, the recent chain grows unbounded:
- **Current threshold:** 10,000 entities (recommended rebuild point)
- **Import rate:** 0.13 entities/sec = ~470 entities/hour
- **Time to threshold:** ~21 hours of continuous import
- **Chain walk time:** Linear growth (O(n)) - 2,295 entries took 3 minutes

### 2. **Query Performance Impact**
Recent chain queries become slower as chain length increases:
- **2,295 entries:** ~3 minutes to walk
- **10,000 entries:** ~13 minutes to walk (estimated)
- **API timeouts:** Queries > 2 minutes may timeout
- **User experience:** Entity listing becomes unusable

### 3. **Memory Usage**
Walking long chains requires holding entries in memory:
- **2,300 entities:** ~9.3 MB CAR size
- **10,000 entities:** ~40 MB estimated
- **Risk:** FastAPI service memory limits (512 MB production)

### 4. **Disaster Recovery**
Longer chains increase backup/restore time:
- **Chain walk:** Must traverse every entry during snapshot
- **CAR export:** Larger snapshots = longer export time
- **Restore time:** Proportional to snapshot size

---

## Recommended Snapshot Frequency

### Option 1: **Time-Based (Recommended for Development)**
**Schedule:** Every 24 hours at low-traffic time (2:00 AM UTC)

**Pros:**
- Predictable behavior
- Simple cron job setup
- Works regardless of import activity

**Cons:**
- May snapshot with very few new entities
- Doesn't respond to burst imports

**Implementation:**
```bash
# crontab entry (run as application user)
0 2 * * * cd /path/to/ipfs-server && ./scripts/build-snapshot.sh >> /var/log/arke-snapshots.log 2>&1
```

### Option 2: **Threshold-Based (Recommended for Production)**
**Trigger:** When `recent_count >= 5,000` entities

**Pros:**
- Responds to actual system load
- No unnecessary snapshots during quiet periods
- Prevents performance degradation

**Cons:**
- Requires monitoring system
- More complex implementation

**Implementation Approaches:**

#### A. **API-Level Warning** (Quick Implementation)
Modify `api/chain.py` to emit warnings:

```python
async def append_to_chain(pi: str, tip_cid: str, ver: int) -> str:
    # ... existing code ...

    # Check if rebuild needed
    if pointer.recent_count >= 5000:
        print(f"WARNING: Recent chain has {pointer.recent_count} items. Rebuild recommended.")
        # Optional: Write to monitoring file
        with open("/tmp/snapshot-needed.flag", "w") as f:
            f.write(str(pointer.recent_count))

    return new_cid
```

Cron job checks for flag:
```bash
*/15 * * * * [ -f /tmp/snapshot-needed.flag ] && cd /path/to/ipfs-server && ./scripts/build-snapshot.sh && rm /tmp/snapshot-needed.flag
```

#### B. **API Endpoint Trigger** (Best for Production)
Add endpoint to `api/main.py`:

```python
@app.post("/snapshot/build")
async def trigger_snapshot_build():
    """
    Manually or programmatically trigger snapshot build.
    Returns immediately and runs in background.
    """
    import subprocess
    subprocess.Popen(["/path/to/scripts/build-snapshot.sh"])
    return {"status": "snapshot build triggered"}
```

External monitoring system calls endpoint when threshold reached.

#### C. **Background Worker** (Most Robust)
Use task queue (Celery, APScheduler) to monitor and trigger:

```python
from apscheduler.schedulers.background import BackgroundScheduler

async def check_and_snapshot():
    pointer = await get_index_pointer()
    if pointer.recent_count >= 5000:
        subprocess.run(["./scripts/build-snapshot.sh"])

scheduler = BackgroundScheduler()
scheduler.add_job(check_and_snapshot, 'interval', minutes=15)
scheduler.start()
```

### Option 3: **Hybrid Approach** (Recommended)
Combine time-based and threshold-based:
- **Daily snapshots:** Every 24 hours (minimum)
- **Threshold snapshots:** When recent_count >= 5,000
- **Emergency threshold:** When recent_count >= 8,000 (stricter limit)

---

## Production Deployment Recommendations

### 1. **Immediate Actions**

#### Deploy Time-Based Snapshots (Week 1)
```bash
# SSH to EC2/server
crontab -e

# Add daily snapshot at 2 AM
0 2 * * * cd /home/ubuntu/ipfs-server && ./scripts/build-snapshot.sh >> /var/log/arke-snapshots.log 2>&1

# Add weekly CAR export (Sundays at 3 AM)
0 3 * * 0 cd /home/ubuntu/ipfs-server && ./scripts/export-car.sh >> /var/log/arke-car-exports.log 2>&1
```

#### Set Up Log Rotation
```bash
# /etc/logrotate.d/arke-snapshots
/var/log/arke-snapshots.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0644 ubuntu ubuntu
}

/var/log/arke-car-exports.log {
    weekly
    rotate 12
    compress
    delaycompress
    notifempty
    create 0644 ubuntu ubuntu
}
```

### 2. **Monitoring Setup**

#### Health Checks
Monitor these metrics:
- **Recent chain length:** Alert if > 7,000
- **Last snapshot age:** Alert if > 48 hours
- **CAR export success:** Alert on failure
- **Disk space:** Alert if < 20% free (snapshots + CAR files)

#### Simple Monitoring Script
```bash
#!/bin/bash
# /home/ubuntu/ipfs-server/scripts/check-snapshot-health.sh

RECENT_COUNT=$(curl -s http://localhost:3000/index-pointer | jq -r '.recent_count')
LAST_SNAPSHOT=$(curl -s http://localhost:3000/index-pointer | jq -r '.snapshot_ts')

if [ "$RECENT_COUNT" -gt 7000 ]; then
    echo "CRITICAL: Recent chain has $RECENT_COUNT entries (threshold: 7000)" | mail -s "Arke Snapshot Alert" admin@example.com
fi

# Check snapshot age (example with date comparison)
# ... add logic to alert if last_snapshot > 48 hours old ...
```

Run via cron every 4 hours:
```bash
0 */4 * * * /home/ubuntu/ipfs-server/scripts/check-snapshot-health.sh
```

### 3. **Backup Strategy**

#### Local Backups
- **Keep:** Last 3 snapshots locally
- **Storage:** `/backups/` directory
- **Cleanup:** Automatic via script

```bash
# Add to export-car.sh after successful export
ls -t backups/arke-*.car | tail -n +4 | xargs -r rm
ls -t backups/arke-*.json | tail -n +4 | xargs -r rm
```

#### Offsite Backups
**Recommended:** S3 bucket for CAR files

```bash
# After export-car.sh completes
aws s3 cp ./backups/arke-*.car s3://your-backup-bucket/ipfs-snapshots/ --region us-east-1

# Or use rclone for other providers
rclone copy ./backups/ remote:arke-backups/ipfs-snapshots/
```

**Retention Policy:**
- Daily snapshots: Keep 30 days
- Weekly snapshots: Keep 12 weeks
- Monthly snapshots: Keep 12 months

---

## Snapshot Build Performance

### Current Benchmarks (2,300 entities)
- **Chain walk:** ~3 minutes (2,295 entries)
- **Previous snapshot load:** < 5 seconds (5 entries, 1 chunk)
- **Merge:** < 1 second
- **Chunking:** < 1 second (single 10K chunk)
- **DAG store:** < 5 seconds
- **Index pointer update:** < 1 second
- **Total:** ~3-4 minutes

### Projected Performance (10,000 entities)
- **Chain walk:** ~13 minutes (linear growth)
- **Previous snapshot load:** ~10 seconds (2,300 entries, 1 chunk)
- **Chunking:** ~2 seconds (still 1 chunk)
- **Total:** ~14-15 minutes

### Projected Performance (100,000 entities)
- **Chain walk:** ~130 minutes if full chain (not recommended!)
- **Previous snapshot load:** ~60 seconds (100K entries, 10 chunks)
- **Chunking:** ~10 seconds (10 chunks)
- **Total:** ~5-10 minutes if recent chain kept < 10K ✅

**Key Insight:** Regular snapshots keep build times under 15 minutes by preventing chain growth.

---

## Troubleshooting

### Snapshot Build Fails

**Symptom:** `build-snapshot.sh` exits with error

**Common Causes:**
1. IPFS node not running
2. Index pointer corrupted
3. Chain entry CID not found
4. Out of disk space

**Resolution:**
```bash
# Check IPFS node
docker compose ps

# Check logs
docker compose logs ipfs

# Verify index pointer
curl http://localhost:3000/index-pointer | jq .

# Check disk space
df -h
```

### CAR Export Fails

**Symptom:** `export-car.sh` fails to create CAR file

**Resolution:**
```bash
# Manually export via Docker
docker exec ipfs-node ipfs dag export <snapshot-cid> > backup.car

# Check snapshot CID exists
docker exec ipfs-node ipfs dag get <snapshot-cid>
```

### Queries Timing Out

**Symptom:** `/entities` endpoint returns 504 or takes > 30 seconds

**Immediate Fix:**
```bash
# Emergency snapshot build
./scripts/build-snapshot.sh

# Verify recent_count reset to 0
curl http://localhost:3000/index-pointer | jq .recent_count
```

**Root Cause:** Recent chain too long (> 10K entries)

---

## Rollback Procedure

If snapshot build causes issues:

### 1. **Check Current State**
```bash
curl http://localhost:3000/index-pointer | jq .
```

### 2. **Verify Old Snapshot Still Works**
Previous snapshot CID is stored in snapshot metadata:
```json
{
  "prev_snapshot": {"/": "baguqee..."}
}
```

### 3. **Manual Rollback (If Needed)**
```bash
# Read previous snapshot CID from latest snapshot
PREV_SNAPSHOT=$(docker exec ipfs-node ipfs dag get <latest-snapshot-cid> | jq -r '.prev_snapshot["/"]')

# Restore to previous snapshot
./scripts/restore-from-car.sh backups/arke-1-*.car $PREV_SNAPSHOT
```

---

## Next Steps for Production

### Week 1: Basic Automation
- [x] Test snapshot build with 2,300 entities ✅
- [x] Verify CAR export ✅
- [ ] Deploy daily cron job (2 AM UTC)
- [ ] Set up log rotation
- [ ] Test manual restore procedure

### Week 2: Monitoring
- [ ] Implement threshold warning in API
- [ ] Set up health check script
- [ ] Configure alerting (email/Slack)
- [ ] Document runbook for operators

### Month 1: Optimization
- [ ] Implement threshold-based triggering
- [ ] Set up offsite S3 backups
- [ ] Test disaster recovery procedure
- [ ] Performance test with 10K entities

### Month 2: Advanced Features
- [ ] Background worker for automatic threshold snapshots
- [ ] Prometheus metrics export
- [ ] Grafana dashboard
- [ ] Automated restore testing

---

## Conclusion

**Current Status:** ✅ Snapshot system working correctly

**Immediate Risk:** ⚠️ No automated snapshots - manual intervention required

**Recommended Action:** Deploy daily cron job ASAP (Week 1)

**Long-term Goal:** Threshold-based automation with offsite backups

---

## Snapshot Frequency Decision Matrix

| Import Rate | Entity Count | Snapshot Frequency | Rationale |
|-------------|--------------|-------------------|-----------|
| Low (<100/day) | < 5K | Weekly | Chain walk stays fast |
| Medium (100-500/day) | 5K-10K | Daily | Prevent threshold breach |
| High (>500/day) | > 10K | Threshold (5K) | Responsive to load |
| Burst imports | Variable | Threshold (5K) + Daily | Cover both scenarios |

**Current System:** Medium import rate → **Recommend daily snapshots**

---

**Document Version:** 1.0
**Author:** Claude Code
**Tested With:** 2,300 entities, 9.3 MB CAR export
**Next Review:** After deploying production automation
