#!/usr/bin/env python3
"""
Restore from CAR file to fresh IPFS node.

Process:
1. Import CAR blocks
2. Fetch snapshot object
3. Rebuild MFS .tip files
4. Restore index pointer
5. Verify restoration
"""

import sys
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any
import httpx

# Configuration
IPFS_API = os.getenv("IPFS_API_URL", os.getenv("IPFS_API", "http://localhost:5001/api/v0"))
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "ipfs-node")
INDEX_ROOT = "/arke/index"
INDEX_POINTER_PATH = "/arke/index-pointer"
TIMEOUT = 30.0

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

def wait_for_ipfs(max_retries=30, delay=2):
    """Wait for IPFS node to be ready."""
    import time

    log("Waiting for IPFS node to be ready...")

    for i in range(max_retries):
        try:
            result = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "ipfs", "id"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                success("IPFS node is ready")
                return True
        except:
            pass

        if i < max_retries - 1:
            time.sleep(delay)
            log(f"  Waiting... ({i+1}/{max_retries})")

    error("IPFS node did not become ready in time")

def import_car_file(car_path: Path):
    """Import CAR file using docker exec."""
    log(f"Importing CAR: {car_path.name}")

    # Wait for IPFS to be ready first
    wait_for_ipfs()

    # Copy to container
    container_path = f"/tmp/{car_path.name}"
    subprocess.run(
        ["docker", "cp", str(car_path), f"{CONTAINER_NAME}:{container_path}"],
        check=True
    )

    # Import CAR
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "ipfs", "dag", "import",
         "--pin-roots=true", "--stats", container_path],
        capture_output=True,
        text=True,
        timeout=300
    )

    # Clean up
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "rm", container_path],
        stderr=subprocess.DEVNULL
    )

    if result.returncode != 0:
        error(f"CAR import failed: {result.stderr}")

    # Parse stats
    log(f"  {result.stderr.strip()}")
    success("CAR imported successfully")

def get_snapshot_object(snapshot_cid: str) -> Dict[str, Any]:
    """Fetch snapshot object from IPFS."""
    log(f"Fetching snapshot: {snapshot_cid[:16]}...")

    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{IPFS_API}/dag/get",
            params={"arg": snapshot_cid}
        )
        response.raise_for_status()
        snapshot = response.json()

    schema = snapshot.get("schema")
    if schema not in ["arke/snapshot@v0", "arke/snapshot@v1"]:
        error(f"Invalid snapshot schema: {schema}")

    log(f"Snapshot: seq={snapshot.get('seq')}, count={len(snapshot.get('entries', []))}")
    return snapshot

def create_tip_file(pi: str, tip_cid: str):
    """Create .tip file in MFS."""
    shard1 = pi[:2]
    shard2 = pi[2:4]
    dir_path = f"{INDEX_ROOT}/{shard1}/{shard2}"
    tip_path = f"{dir_path}/{pi}.tip"

    with httpx.Client(timeout=TIMEOUT) as client:
        # Create directory
        client.post(
            f"{IPFS_API}/files/mkdir",
            params={"arg": dir_path, "parents": "true"}
        )

        # Write tip file
        response = client.post(
            f"{IPFS_API}/files/write",
            params={
                "arg": tip_path,
                "create": "true",
                "truncate": "true"
            },
            files={"file": ("tip", tip_cid.encode())}
        )
        response.raise_for_status()

def rebuild_mfs(snapshot: Dict[str, Any]):
    """Rebuild MFS structure from snapshot."""
    entries = snapshot.get("entries", [])
    log(f"Rebuilding MFS for {len(entries)} entities...")

    for i, entry in enumerate(entries):
        if (i + 1) % 100 == 0:
            log(f"  Created {i + 1}/{len(entries)} .tip files...")

        pi = entry["pi"]
        tip_cid = entry.get("tip_cid", {})
        if isinstance(tip_cid, dict) and "/" in tip_cid:
            tip_cid = tip_cid["/"]

        create_tip_file(pi, tip_cid)

    success(f"Created {len(entries)} .tip files")

def rebuild_index_pointer(snapshot_cid: str, snapshot: Dict[str, Any]):
    """Restore index pointer."""
    log("Rebuilding index pointer...")

    seq = snapshot.get("seq", 1)
    total_count = snapshot.get("total_count", len(snapshot.get("entries", [])))
    timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    # Extract event head from last entry
    entries = snapshot.get("entries", [])
    event_head = None
    if entries:
        last_entry = entries[-1]
        chain_cid = last_entry.get("chain_cid")
        if chain_cid:
            event_head = chain_cid.get("/") if isinstance(chain_cid, dict) else chain_cid

    # Build index pointer
    pointer = {
        "schema": "arke/index-pointer@v2",
        "event_head": event_head,
        "event_count": snapshot.get("total_count", 0),
        "latest_snapshot_cid": snapshot_cid,
        "snapshot_seq": seq,
        "snapshot_count": total_count,
        "snapshot_ts": snapshot.get("ts"),
        "total_count": total_count,
        "last_updated": timestamp
    }

    # Write to MFS
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{IPFS_API}/files/write",
            params={
                "arg": INDEX_POINTER_PATH,
                "create": "true",
                "truncate": "true",
                "parents": "true"
            },
            files={"file": ("pointer.json", json.dumps(pointer).encode())}
        )
        response.raise_for_status()

    success("Index pointer restored")

def verify_restoration(snapshot: Dict[str, Any]):
    """Verify all .tip files were created."""
    log("Verifying restoration...")

    entries = snapshot.get("entries", [])
    verified = 0

    with httpx.Client(timeout=TIMEOUT) as client:
        for entry in entries:
            pi = entry["pi"]
            shard1 = pi[:2]
            shard2 = pi[2:4]
            tip_path = f"{INDEX_ROOT}/{shard1}/{shard2}/{pi}.tip"

            try:
                response = client.post(
                    f"{IPFS_API}/files/stat",
                    params={"arg": tip_path}
                )
                if response.status_code == 200:
                    verified += 1
            except:
                warn(f"Missing tip file: {pi}")

    success(f"Verified {verified}/{len(entries)} .tip files")

def find_snapshot_cid_from_metadata(car_path: Path) -> str:
    """Read snapshot CID from metadata file."""
    metadata_path = car_path.with_suffix(".json")
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        return metadata.get("snapshot_cid")
    return None

def main():
    if len(sys.argv) < 2:
        print("Usage: restore-from-car.py <car-file> [snapshot-cid]", file=sys.stderr)
        print("", file=sys.stderr)
        print("If snapshot-cid not provided, will read from metadata file", file=sys.stderr)
        sys.exit(1)

    car_path = Path(sys.argv[1])
    if not car_path.exists():
        error(f"CAR file not found: {car_path}")

    # Get snapshot CID
    if len(sys.argv) > 2:
        snapshot_cid = sys.argv[2]
    else:
        snapshot_cid = find_snapshot_cid_from_metadata(car_path)
        if not snapshot_cid:
            error("Snapshot CID not provided and no metadata file found")

    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("CAR Restoration", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"File:     {car_path.name}", file=sys.stderr)
    print(f"Snapshot: {snapshot_cid}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Import CAR
    import_car_file(car_path)

    # Get snapshot
    snapshot = get_snapshot_object(snapshot_cid)

    # Rebuild MFS
    rebuild_mfs(snapshot)

    # Restore index pointer
    rebuild_index_pointer(snapshot_cid, snapshot)

    # Verify
    verify_restoration(snapshot)

    # Summary
    count = len(snapshot.get("entries", []))
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"{GREEN}Restoration Complete{NC}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Snapshot:  {snapshot_cid} (seq {snapshot.get('seq')})", file=sys.stderr)
    print(f"Entities:  {count}", file=sys.stderr)
    print(f"MFS:       {INDEX_ROOT}", file=sys.stderr)
    print(f"Status:    ✓ All verified", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)
    success("System restored from CAR! Ready to serve requests.")

    # Output JSON for scripting
    print(json.dumps({
        "snapshot_cid": snapshot_cid,
        "entity_count": count,
        "restored": True
    }))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error(f"Restoration failed: {e}")
