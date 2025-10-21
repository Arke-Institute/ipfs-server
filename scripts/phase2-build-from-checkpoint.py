#!/usr/bin/env python3
"""Phase 2: Build and store snapshot from existing checkpoint file."""

import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import httpx

IPFS_API = "http://ipfs:5001/api/v0"
INDEX_POINTER_PATH = "/arke/index-pointer"
SNAPSHOTS_DIR = Path("./snapshots")
CHECKPOINT_FILE = Path("/tmp/snapshot-entries.ndjson")

print("[INFO] Building snapshot from checkpoint file...")

# Read all entries
entries = []
with open(CHECKPOINT_FILE) as f:
    for line in f:
        entries.append(json.loads(line))

# Reverse to chronological order
entries.reverse()

print(f"[INFO] Loaded {len(entries)} entries")

# Get index pointer
with httpx.Client(timeout=30.0) as client:
    response = client.post(f"{IPFS_API}/files/read", params={"arg": INDEX_POINTER_PATH})
    pointer = response.json()

event_head = pointer.get("event_head")
prev_seq = pointer.get("snapshot_seq", 0)
new_seq = prev_seq + 1
timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

# Build snapshot
snapshot = {
    "schema": "arke/snapshot@v1",
    "seq": new_seq,
    "ts": timestamp,
    "event_cid": event_head,
    "total_count": len(entries),
    "entries": entries
}

prev_snapshot = pointer.get("latest_snapshot_cid")
if prev_snapshot:
    snapshot["prev_snapshot"] = {"/": prev_snapshot}
else:
    snapshot["prev_snapshot"] = None

# Write to temp file
temp_file = Path("/tmp/snapshot.json")
temp_file.write_text(json.dumps(snapshot, indent=2))

print(f"[INFO] Snapshot JSON size: {temp_file.stat().st_size / 1024 / 1024:.2f} MB")
print(f"[INFO] Storing via ipfs dag put...")

# Use ipfs CLI
result = subprocess.run(
    ["ipfs", "dag", "put", "--store-codec=dag-json", "--input-codec=json", "--pin=true"],
    stdin=open(temp_file),
    capture_output=True,
    text=True,
    timeout=300
)

if result.returncode != 0:
    print(f"[ERROR] ipfs dag put failed: {result.stderr}")
    exit(1)

output = json.loads(result.stdout.strip())
snapshot_cid = output["Cid"]["/"]

print(f"[SUCCESS] Snapshot stored: {snapshot_cid}")

# Update index pointer
new_pointer = {
    "schema": "arke/index-pointer@v2",
    "event_head": event_head,
    "event_count": pointer.get("event_count", 0),
    "latest_snapshot_cid": snapshot_cid,
    "snapshot_event_cid": event_head,
    "snapshot_seq": new_seq,
    "snapshot_count": len(entries),
    "snapshot_ts": timestamp,
    "total_count": len(entries),
    "last_updated": timestamp
}

with httpx.Client(timeout=600.0) as client:
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

print("[SUCCESS] Index pointer updated")

# Save metadata
SNAPSHOTS_DIR.mkdir(exist_ok=True)
metadata = {
    "cid": snapshot_cid,
    "seq": new_seq,
    "ts": timestamp,
    "count": len(entries)
}

(SNAPSHOTS_DIR / f"snapshot-{new_seq}.json").write_text(json.dumps(metadata, indent=2))
(SNAPSHOTS_DIR / "latest.json").write_text(json.dumps(metadata, indent=2))

print("[SUCCESS] Snapshot metadata saved")
print(f"\nSnapshot CID: {snapshot_cid}")
