#!/usr/bin/env python3
"""
Build snapshot from event chain with streaming approach.
Writes entries incrementally to avoid memory issues and provide progress visibility.
"""

import sys
import json
import os
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import httpx

# Configuration
IPFS_API = os.getenv("IPFS_API_URL", "http://localhost:5001/api/v0")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "ipfs-node")
INDEX_POINTER_PATH = os.getenv("INDEX_POINTER_PATH", "/arke/index-pointer")
SNAPSHOTS_DIR = Path(os.getenv("SNAPSHOTS_DIR", "./snapshots"))
CHECKPOINT_FILE = Path("/tmp/snapshot-entries.ndjson")
LOCK_FILE = Path("/tmp/arke-snapshot.lock")

# Progress logging
LOG_INTERVAL = 100
TIMEOUT = 30.0  # HTTP timeout for individual requests

# Colors
BLUE = '\033[0;34m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
NC = '\033[0m'

def log(msg: str):
    print(f"{BLUE}[INFO]{NC} {msg}", file=sys.stderr)

def success(msg: str):
    print(f"{GREEN}[SUCCESS]{NC} {msg}", file=sys.stderr)

def warn(msg: str):
    print(f"{YELLOW}[WARN]{NC} {msg}", file=sys.stderr)

def error(msg: str):
    print(f"{RED}[ERROR]{NC} {msg}", file=sys.stderr)
    sys.exit(1)

def check_lock():
    """Check for existing lock file."""
    if LOCK_FILE.exists():
        lock_age = time.time() - LOCK_FILE.stat().st_mtime
        if lock_age < 600:  # 10 minutes
            error(f"Snapshot build already in progress (lock file exists: {LOCK_FILE})")
        else:
            warn(f"Removing stale lock file (age: {lock_age:.0f}s)")
            LOCK_FILE.unlink()

    # Create lock
    LOCK_FILE.write_text(f"{os.getpid()}|{int(time.time())}")

def cleanup_lock():
    """Remove lock file."""
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()

def get_index_pointer() -> Dict[str, Any]:
    """Read index pointer from MFS."""
    log("Reading index pointer from MFS...")
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                f"{IPFS_API}/files/read",
                params={"arg": INDEX_POINTER_PATH}
            )
            response.raise_for_status()
            pointer = response.json()
            log(f"Index pointer: event_head={pointer.get('event_head', 'none')[:16]}..., "
                f"event_count={pointer.get('event_count', 0)}, "
                f"total_count={pointer.get('total_count', 0)}")
            return pointer
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 500:  # File doesn't exist
            return {}
        raise
    except Exception as e:
        error(f"Failed to read index pointer: {e}")

def walk_event_chain(event_head: str, checkpoint_file: Path) -> int:
    """
    Walk event chain and write entries to checkpoint file.
    Returns count of unique PIs processed.
    """
    log(f"Walking event chain from head: {event_head[:16]}...")
    log(f"Checkpoint file: {checkpoint_file}")

    current = event_head
    count = 0
    seen_pis = set()
    start_time = time.time()

    with httpx.Client(timeout=TIMEOUT) as client:
        with open(checkpoint_file, 'w') as f:
            while current:
                # Fetch event
                try:
                    response = client.post(
                        f"{IPFS_API}/dag/get",
                        params={"arg": current}
                    )
                    response.raise_for_status()
                    event = response.json()
                except Exception as e:
                    warn(f"Failed to fetch event {current[:16]}: {e}")
                    break

                # Extract PI
                pi = event.get("pi")
                event_type = event.get("type")
                ts = event.get("ts")

                if not pi:
                    warn(f"Event {current[:16]} has no PI, skipping")
                    prev_obj = event.get("prev")
                    if not prev_obj:
                        break
                    current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj
                    if not current:
                        break
                    continue

                # Skip if already seen
                if pi in seen_pis:
                    prev_obj = event.get("prev")
                    if not prev_obj:
                        break
                    current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj
                    if not current:
                        break
                    continue

                seen_pis.add(pi)
                count += 1

                # Read current tip from MFS
                shard1 = pi[:2]
                shard2 = pi[2:4]
                tip_path = f"/arke/index/{shard1}/{shard2}/{pi}.tip"

                try:
                    response = client.post(
                        f"{IPFS_API}/files/read",
                        params={"arg": tip_path}
                    )
                    response.raise_for_status()
                    tip_cid = response.text.strip()
                except Exception as e:
                    warn(f"Failed to read tip for {pi}: {e}")
                    prev_obj = event.get("prev")
                    if not prev_obj:
                        break
                    current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj
                    if not current:
                        break
                    continue

                # Fetch manifest to get version
                try:
                    response = client.post(
                        f"{IPFS_API}/dag/get",
                        params={"arg": tip_cid}
                    )
                    response.raise_for_status()
                    manifest = response.json()
                    ver = manifest.get("ver", 0)
                except Exception as e:
                    warn(f"Failed to fetch manifest for {pi}: {e}")
                    ver = 0

                # Write entry to checkpoint file
                entry = {
                    "pi": pi,
                    "ver": ver,
                    "tip_cid": {"/": tip_cid},
                    "ts": ts,
                    "chain_cid": {"/": current}
                }
                f.write(json.dumps(entry) + "\n")

                # Progress logging
                if count % LOG_INTERVAL == 0:
                    elapsed = time.time() - start_time
                    rate = count / elapsed
                    log(f"Processed {count} unique PIs ({rate:.1f} entries/sec, {elapsed:.0f}s elapsed)")

                # Move to previous event
                prev_obj = event.get("prev")
                if not prev_obj:
                    break
                current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj
                if not current:
                    break

    elapsed = time.time() - start_time
    success(f"Collected {count} unique PIs in {elapsed:.0f}s ({count/elapsed:.1f} entries/sec)")
    return count

def build_snapshot_json(checkpoint_file: Path, pointer: Dict[str, Any], seq: int, timestamp: str, total_count: int) -> Dict[str, Any]:
    """Build final snapshot JSON from checkpoint file."""
    log("Building snapshot JSON from checkpoint file...")

    # Read all entries
    entries = []
    with open(checkpoint_file) as f:
        for line in f:
            entries.append(json.loads(line))

    # Reverse to get chronological order (oldest first)
    entries.reverse()

    log(f"Loaded {len(entries)} entries from checkpoint")

    # Build snapshot object
    snapshot = {
        "schema": "arke/snapshot@v1",
        "seq": seq,
        "ts": timestamp,
        "event_cid": pointer.get("event_head"),
        "total_count": total_count,
        "entries": entries
    }

    # Add prev_snapshot link if exists
    prev_snapshot = pointer.get("latest_snapshot_cid")
    if prev_snapshot:
        snapshot["prev_snapshot"] = {"/": prev_snapshot}
    else:
        snapshot["prev_snapshot"] = None

    return snapshot

def store_snapshot_ipfs_cli(snapshot: Dict[str, Any]) -> str:
    """Store snapshot using ipfs CLI via docker exec."""
    log("Storing snapshot via ipfs CLI...")

    snapshot_json = json.dumps(snapshot, indent=2)
    log(f"Snapshot JSON size: {len(snapshot_json) / 1024 / 1024:.2f} MB")

    # Use docker exec to run ipfs dag put
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", CONTAINER_NAME, "ipfs", "dag", "put",
             "--store-codec=dag-json", "--input-codec=json", "--pin=true",
             "--allow-big-block"],
            input=snapshot_json,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout for large files
        )

        if result.returncode != 0:
            error(f"ipfs dag put failed: {result.stderr}")

        # Parse CID from output (plain CID string)
        snapshot_cid = result.stdout.strip()

        success(f"Snapshot stored: {snapshot_cid}")
        return snapshot_cid

    except subprocess.TimeoutExpired:
        error("ipfs dag put timed out after 5 minutes")
    except Exception as e:
        error(f"Failed to store snapshot: {e}")

def update_index_pointer(pointer: Dict[str, Any], snapshot_cid: str, seq: int, timestamp: str, total_count: int):
    """Update index pointer with new snapshot metadata."""
    log("Updating index pointer...")

    # Preserve event_head and event_count
    new_pointer = {
        "schema": "arke/index-pointer@v2",
        "event_head": pointer.get("event_head"),
        "event_count": pointer.get("event_count", 0),
        "latest_snapshot_cid": snapshot_cid,
        "snapshot_event_cid": pointer.get("event_head"),
        "snapshot_seq": seq,
        "snapshot_count": total_count,
        "snapshot_ts": timestamp,
        "total_count": total_count,
        "last_updated": timestamp
    }

    # Write to MFS
    with httpx.Client(timeout=600.0) as client:  # Long timeout for large operations
        response = client.post(
            f"{IPFS_API}/files/write",
            params={
                "arg": INDEX_POINTER_PATH,
                "create": "true",
                "truncate": "true",
                "parents": "true"
            },
            files={"file": ("pointer.json", json.dumps(new_pointer).encode(), "application/json")}
        )
        response.raise_for_status()

    success("Index pointer updated")

def save_metadata(snapshot_cid: str, seq: int, timestamp: str, total_count: int):
    """Save snapshot metadata to local files."""
    SNAPSHOTS_DIR.mkdir(exist_ok=True)

    metadata = {
        "cid": snapshot_cid,
        "seq": seq,
        "ts": timestamp,
        "count": total_count
    }

    # Save versioned and latest
    (SNAPSHOTS_DIR / f"snapshot-{seq}.json").write_text(json.dumps(metadata, indent=2))
    (SNAPSHOTS_DIR / "latest.json").write_text(json.dumps(metadata, indent=2))

    success("Snapshot metadata saved")

def main():
    start_time = time.time()

    try:
        check_lock()

        # Get current state
        pointer = get_index_pointer()
        event_head = pointer.get("event_head")

        if not event_head:
            error("No event head found in index pointer")

        prev_seq = pointer.get("snapshot_seq", 0)
        new_seq = prev_seq + 1
        timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        log(f"Starting snapshot build (seq={new_seq})")

        # Phase 1: Walk event chain and write to checkpoint
        log("=" * 60)
        log("PHASE 1: Collecting entries from event chain")
        log("=" * 60)

        total_count = walk_event_chain(event_head, CHECKPOINT_FILE)

        if total_count == 0:
            error("No entries collected")

        # Phase 2: Build and store snapshot
        log("")
        log("=" * 60)
        log("PHASE 2: Building and storing snapshot")
        log("=" * 60)

        snapshot = build_snapshot_json(CHECKPOINT_FILE, pointer, new_seq, timestamp, total_count)
        snapshot_cid = store_snapshot_ipfs_cli(snapshot)

        # Phase 3: Update metadata
        log("")
        log("=" * 60)
        log("PHASE 3: Updating metadata")
        log("=" * 60)

        update_index_pointer(pointer, snapshot_cid, new_seq, timestamp, total_count)
        save_metadata(snapshot_cid, new_seq, timestamp, total_count)

        # Summary
        elapsed = time.time() - start_time
        print("", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"{GREEN}Snapshot Build Complete{NC}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"CID:      {snapshot_cid}", file=sys.stderr)
        print(f"Sequence: {new_seq}", file=sys.stderr)
        print(f"Entities: {total_count}", file=sys.stderr)
        print(f"Time:     {timestamp}", file=sys.stderr)
        print(f"Duration: {elapsed:.0f}s ({elapsed/60:.1f} minutes)", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("", file=sys.stderr)

        # Output CID to stdout (for scripting)
        print(snapshot_cid)

    except KeyboardInterrupt:
        warn("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        error(f"Unexpected error: {e}")
    finally:
        cleanup_lock()

if __name__ == "__main__":
    main()
