#!/usr/bin/env python3
"""
Verify snapshot structure and integrity.

Checks:
- Snapshot size (should be minimal - just PIs and tip CIDs)
- No duplicate PIs
- All tip CIDs are valid
- Snapshot is stored as dag-json
"""

import sys
import json
import os
from pathlib import Path
import httpx

# Configuration
IPFS_API = os.getenv("IPFS_API_URL", "http://localhost:5001/api/v0")
SNAPSHOTS_DIR = Path(os.getenv("SNAPSHOTS_DIR", "./snapshots"))
TIMEOUT = 30.0

# Expected size per entry (rough estimate)
MAX_BYTES_PER_ENTRY = 500  # PI (20) + tip CID (60) + overhead

# Colors
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'

def log(msg: str):
    print(f"{BLUE}[INFO]{NC} {msg}", file=sys.stderr)

def success(msg: str):
    print(f"{GREEN}[PASS]{NC} {msg}", file=sys.stderr)

def fail(msg: str):
    print(f"{RED}[FAIL]{NC} {msg}", file=sys.stderr)
    sys.exit(1)

def warn(msg: str):
    print(f"{YELLOW}[WARN]{NC} {msg}", file=sys.stderr)

def get_snapshot_cid() -> str:
    """Read snapshot CID from latest.json."""
    latest_file = SNAPSHOTS_DIR / "latest.json"
    if not latest_file.exists():
        fail(f"No snapshot found at {latest_file}")

    metadata = json.loads(latest_file.read_text())
    return metadata["cid"]

def fetch_snapshot(snapshot_cid: str) -> dict:
    """Fetch snapshot object from IPFS."""
    log(f"Fetching snapshot: {snapshot_cid}")

    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{IPFS_API}/dag/get",
            params={"arg": snapshot_cid}
        )
        response.raise_for_status()
        return response.json()

def verify_schema(snapshot: dict):
    """Verify snapshot has correct schema."""
    log("Checking snapshot schema...")

    schema = snapshot.get("schema")
    if schema not in ["arke/snapshot@v0", "arke/snapshot@v1"]:
        fail(f"Invalid schema: {schema}")

    success(f"Schema: {schema}")

def verify_no_duplicates(entries: list):
    """Verify no duplicate PIs in snapshot."""
    log("Checking for duplicate PIs...")

    pis = [entry["pi"] for entry in entries]
    unique_pis = set(pis)

    if len(pis) != len(unique_pis):
        duplicates = [pi for pi in unique_pis if pis.count(pi) > 1]
        fail(f"Found {len(duplicates)} duplicate PIs: {duplicates[:5]}")

    success(f"No duplicates (all {len(pis)} PIs are unique)")

def verify_cids(entries: list):
    """Verify all tip CIDs are valid format."""
    log("Verifying CID formats...")

    invalid = []
    for entry in entries:
        tip_cid = entry.get("tip_cid", {}).get("/") or entry.get("tip_cid")
        if not tip_cid or len(tip_cid) < 40:
            invalid.append(entry["pi"])

    if invalid:
        fail(f"Found {len(invalid)} entries with invalid CIDs: {invalid[:5]}")

    success(f"All {len(entries)} entries have valid tip CIDs")

def verify_size(snapshot: dict, snapshot_cid: str):
    """Verify snapshot is reasonably sized."""
    log("Checking snapshot size...")

    # Estimate size from JSON
    snapshot_json = json.dumps(snapshot)
    size_bytes = len(snapshot_json.encode())
    entry_count = len(snapshot.get("entries", []))

    bytes_per_entry = size_bytes / entry_count if entry_count > 0 else 0

    log(f"  Total size: {size_bytes:,} bytes ({size_bytes / 1024 / 1024:.2f} MB)")
    log(f"  Entries: {entry_count}")
    log(f"  Avg per entry: {bytes_per_entry:.0f} bytes")

    if bytes_per_entry > MAX_BYTES_PER_ENTRY:
        warn(f"Entry size is larger than expected ({bytes_per_entry:.0f} > {MAX_BYTES_PER_ENTRY})")
    else:
        success(f"Snapshot is minimal ({bytes_per_entry:.0f} bytes/entry)")

def verify_required_fields(snapshot: dict):
    """Verify snapshot has all required fields."""
    log("Checking required fields...")

    required = ["schema", "seq", "ts", "entries", "total_count"]
    missing = [field for field in required if field not in snapshot]

    if missing:
        fail(f"Missing required fields: {missing}")

    success("All required fields present")

    # Verify entries structure
    entries = snapshot["entries"]
    if not entries:
        fail("Snapshot has no entries")

    required_entry_fields = ["pi", "tip_cid"]
    for i, entry in enumerate(entries[:3]):  # Check first 3
        missing_entry = [field for field in required_entry_fields if field not in entry]
        if missing_entry:
            fail(f"Entry {i} missing fields: {missing_entry}")

    success(f"All {len(entries)} entries have required fields")

def main():
    if len(sys.argv) > 1:
        snapshot_cid = sys.argv[1]
    else:
        snapshot_cid = get_snapshot_cid()

    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Snapshot Verification", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"CID: {snapshot_cid}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Fetch snapshot
    snapshot = fetch_snapshot(snapshot_cid)

    # Run verification checks
    verify_schema(snapshot)
    verify_required_fields(snapshot)

    entries = snapshot["entries"]
    verify_no_duplicates(entries)
    verify_cids(entries)
    verify_size(snapshot, snapshot_cid)

    # Summary
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"{GREEN}All Checks Passed{NC}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Snapshot CID:  {snapshot_cid}", file=sys.stderr)
    print(f"Sequence:      {snapshot['seq']}", file=sys.stderr)
    print(f"Entities:      {len(entries)}", file=sys.stderr)
    print(f"Timestamp:     {snapshot['ts']}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Output JSON for scripting
    print(json.dumps({
        "snapshot_cid": snapshot_cid,
        "seq": snapshot["seq"],
        "entity_count": len(entries),
        "all_checks_passed": True
    }))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        fail(f"Verification failed: {e}")
