# Snapshot Bug Analysis - Race Condition

## The Problem

When entities are added rapidly (continuous import), the snapshot system has a critical race condition that causes:
1. **Trigger spam**: Every entity after 100 triggers a new snapshot
2. **Wrong counts**: Snapshots capture partial state (81 instead of expected counts)
3. **Multiple concurrent builds**: Snapshot builds overlap with ongoing imports

## Root Cause

### The Race Condition Flow

```
Time  | Recent Count | Event                           | What Happens
------|--------------|--------------------------------|------------------
T0    | 99           | Entity added                   | recent_count = 99
T1    | 100          | Entity added                   | Threshold reached (100 >= 100)
      |              | → Snapshot build STARTS        | Subprocess spawned (fire-and-forget)
T2    | 101          | Entity added (during build)    | recent_count = 101, threshold check (101 >= 100) → TRIGGER AGAIN!
T3    | 102          | Entity added (during build)    | recent_count = 102, threshold check (102 >= 100) → TRIGGER AGAIN!
T4    | 103          | Entity added (during build)    | recent_count = 103, threshold check (103 >= 100) → TRIGGER AGAIN!
...   | ...          | ...                            | ...
T50   | 181          | Snapshot build COMPLETES       | Walks chain, gets 181 entities
      |              | → Updates index pointer:       | recent_count = 0, snapshot_count = 181
T51   | 1            | Entity added                   | recent_count = 1 (after reset)
```

### Evidence from Logs

```
⚠️  Threshold reached: 163/100 entities
🔄 Triggering automatic snapshot build...
✅ Snapshot build triggered in background
⚠️  Threshold reached: 164/100 entities
🔄 Triggering automatic snapshot build...
✅ Snapshot build triggered in background
⚠️  Threshold reached: 165/100 entities
...
```

**Every single entity** after 100 triggers a new snapshot build!

### Why This Happens

Look at the trigger logic in `api/chain.py:54-78`:

```python
# 5. Check if rebuild needed and auto-trigger
if pointer.recent_count >= settings.REBUILD_THRESHOLD:  # Line 54
    print(f"⚠️  Threshold reached: {pointer.recent_count}/{settings.REBUILD_THRESHOLD} entities")

    if settings.AUTO_SNAPSHOT:
        print("🔄 Triggering automatic snapshot build...")

        # Trigger snapshot build in background (fire-and-forget)
        try:
            subprocess.Popen(...)  # Spawns background process
            print("✅ Snapshot build triggered in background")
        except Exception as e:
            print(f"❌ Failed to trigger snapshot build: {e}")
```

**The problem:**
1. `recent_count` is checked BEFORE snapshot completes
2. Snapshot runs in background (fire-and-forget)
3. While snapshot is building (walking chain, taking time), new entities keep being added
4. Each new entity increments `recent_count` (now 101, 102, 103...)
5. Each passes the threshold check (`101 >= 100`, `102 >= 100`, etc.)
6. Each spawns ANOTHER snapshot build!

### Why Snapshots Have Wrong Counts

When snapshot builds overlap:

**Snapshot 1** (started at recent_count=100):
- Walks chain, finds 181 entities (by the time it walks, more were added)
- Updates pointer: `recent_count = 0`, `snapshot_count = 181`

**Snapshot 2** (started at recent_count=101, while Snapshot 1 still running):
- Reads old index pointer (before Snapshot 1 finishes)
- Walks chain from old `chain_head`
- By the time it runs, Snapshot 1 already completed
- Reads updated pointer that shows `recent_count = 0`
- Walks chain, but chain is shorter now (or reads stale state)
- Creates snapshot with fewer entries (81)

**Snapshot 3** (started at recent_count=102):
- Similar confusion with race conditions
- Captures whatever state exists when it runs

## Why You're Seeing These Numbers

### Index Pointer State
```json
{
  "snapshot_seq": 3,
  "snapshot_count": 81,      ← Last snapshot (wrong count)
  "recent_count": 104,       ← 104 new entities since snapshot 3
  "total_count": 185         ← 81 (snapshot) + 104 (recent)
}
```

### Expected vs Actual

**Expected:**
- ~600 entities added
- Should trigger ~6 snapshots (every 100)
- Each snapshot should have ~100 entities (except last might be partial)

**Actual:**
- 3 snapshots created
- Snapshot counts: unknown (1), unknown (2), 81 (3)
- Hundreds of snapshot triggers (one per entity after threshold!)
- Most snapshot processes probably failed or got killed

## The Missing Piece: Where Did The Entities Go?

Let me check the actual snapshot CIDs to see what they contain:

```bash
# Snapshot 1
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=<snapshot-1-cid>" | jq '.entries | length'

# Snapshot 2
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=<snapshot-2-cid>" | jq '.entries | length'
```

The metadata files only have:
```json
{"cid": "...", "seq": 1, "ts": "...", "count": <actual count>}
```

But they don't show the `latest_snapshot_cid` field properly - that's stored separately.

## The Fix

We need to prevent multiple concurrent snapshot builds. Two approaches:

### Option 1: Lock File (Simple)

Add a lock file check before triggering:

```python
# In chain.py
SNAPSHOT_LOCK = "/tmp/snapshot.lock"

if pointer.recent_count >= settings.REBUILD_THRESHOLD:
    # Check if snapshot already building
    if os.path.exists(SNAPSHOT_LOCK):
        print("⚠️  Snapshot already building, skipping trigger")
        return new_cid

    # Create lock file
    with open(SNAPSHOT_LOCK, 'w') as f:
        f.write(str(time.time()))

    # Trigger snapshot (which removes lock when done)
    subprocess.Popen(...)
```

And in `build-snapshot.sh`:
```bash
# At start
LOCK_FILE="/tmp/snapshot.lock"
if [[ -f "$LOCK_FILE" ]]; then
  error "Snapshot already building (lock file exists)"
fi
trap "rm -f $LOCK_FILE" EXIT

# At end (in trap)
rm -f "$LOCK_FILE"
```

### Option 2: Database/Flag in Index Pointer (Better)

Add a `snapshot_building` flag to the index pointer:

```json
{
  "schema": "arke/index-pointer@v1",
  "snapshot_building": false,  // ← New field
  "recent_count": 100,
  ...
}
```

**In chain.py:**
```python
if pointer.recent_count >= settings.REBUILD_THRESHOLD:
    if pointer.snapshot_building:
        print("⚠️  Snapshot already building, skipping trigger")
        return new_cid

    # Set flag
    pointer.snapshot_building = True
    await index_pointer.update_index_pointer(pointer)

    # Trigger snapshot
    subprocess.Popen(...)
```

**In build-snapshot.sh:**
```bash
# At start: check flag
local building=$(echo "$pointer" | jq -r '.snapshot_building // false')
if [[ "$building" == "true" ]]; then
  error "Snapshot already building"
fi

# Set flag to true
# ... update index pointer with snapshot_building=true

# At end: clear flag in final index pointer update
# Line 198: Add snapshot_building field
local new_pointer=$(jq -n \
  ... \
  '{
    ...
    snapshot_building: false  // ← Clear flag
  }')
```

### Option 3: Check Process List (Hacky)

Before spawning, check if `build-snapshot.sh` is already running:

```python
# Check for existing process
result = subprocess.run(
    ["docker", "exec", "ipfs-node", "pgrep", "-f", "build-snapshot.sh"],
    capture_output=True
)
if result.returncode == 0:
    print("⚠️  Snapshot already building, skipping trigger")
    return new_cid

# Spawn new process
subprocess.Popen(...)
```

## Recommended Solution

**Option 1 (Lock File)** is simplest and most reliable:
- Works across container restarts (if using volume-mounted /tmp)
- No database schema changes needed
- Atomic file system operations
- Easy to debug (just check if file exists)

## Additional Improvements Needed

### 1. Reset Trigger After Snapshot Starts

Even with a lock, we should reset the trigger condition immediately:

```python
if pointer.recent_count >= settings.REBUILD_THRESHOLD:
    if lock_exists():
        return new_cid  # Skip if already building

    # IMPORTANT: Reset recent_count IMMEDIATELY (optimistic)
    # This prevents subsequent appends from re-triggering
    # The snapshot build will set the correct count when it completes
    pointer.recent_count = 0  # Reset NOW
    await index_pointer.update_index_pointer(pointer)

    # Then trigger snapshot
    subprocess.Popen(...)
```

**Problem with this:** If snapshot build fails, we've lost the count.

**Better approach:** Add a `last_snapshot_trigger` timestamp:

```json
{
  "recent_count": 104,
  "last_snapshot_trigger": "2025-10-12T05:11:00Z"  // When we last triggered
}
```

```python
# Only trigger if we haven't triggered recently (e.g., within last 60 seconds)
now = datetime.now(timezone.utc)
last_trigger = pointer.get('last_snapshot_trigger')

if last_trigger:
    last_trigger_dt = datetime.fromisoformat(last_trigger.replace('Z', '+00:00'))
    if (now - last_trigger_dt).total_seconds() < 60:
        print("⚠️  Snapshot triggered recently, skipping")
        return new_cid

# Update trigger time
pointer.last_snapshot_trigger = now.isoformat().replace('+00:00', 'Z')
await index_pointer.update_index_pointer(pointer)

# Trigger snapshot
subprocess.Popen(...)
```

### 2. Log Snapshot Build Output

The fire-and-forget approach means we never see errors. Should log to a file:

```python
subprocess.Popen(
    [script_path],
    stdout=open('/app/logs/snapshot-build.log', 'a'),
    stderr=subprocess.STDOUT,  # Combine stderr with stdout
    ...
)
```

### 3. Snapshot Build Duration Monitoring

Add timing to see how long builds take:

```bash
# In build-snapshot.sh
START_TIME=$(date +%s)

# ... build logic

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
success "Snapshot completed in ${DURATION}s"
```

## Summary

**Root cause:** No mutex/lock prevents concurrent snapshot builds during rapid imports.

**Symptoms:**
- Hundreds of snapshot triggers (one per entity after threshold)
- Overlapping snapshot builds
- Wrong entity counts in snapshots
- Confusing state

**Fix:** Add lock file check before triggering snapshot build.

**Additional fixes:**
- Add `last_snapshot_trigger` timestamp to prevent re-triggering within cooldown period
- Log snapshot build output to file for debugging
- Add duration monitoring
