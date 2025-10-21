#!/usr/bin/env python3
"""
Verify CAR file contains all expected content.

Imports CAR to temporary IPFS repo and verifies:
- Snapshot object exists
- All manifests accessible (version history)
- All components exist
- Event chain complete
"""

import sys
import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, Set
import httpx

# Configuration
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
    print(f"{GREEN}[PASS]{NC} {msg}", file=sys.stderr)

def fail(msg: str):
    print(f"{RED}[FAIL]{NC} {msg}", file=sys.stderr)
    sys.exit(1)

def warn(msg: str):
    print(f"{YELLOW}[WARN]{NC} {msg}", file=sys.stderr)

class TempIPFSRepo:
    """Context manager for temporary IPFS repository."""

    def __init__(self):
        self.repo_dir = None
        self.api_port = None

    def __enter__(self):
        # Create temp directory
        self.repo_dir = Path(tempfile.mkdtemp(prefix="ipfs-verify-"))
        log(f"Created temp IPFS repo: {self.repo_dir}")

        # Initialize IPFS repo
        env = os.environ.copy()
        env["IPFS_PATH"] = str(self.repo_dir)

        subprocess.run(
            ["ipfs", "init"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # Start daemon on random port
        self.daemon_process = subprocess.Popen(
            ["ipfs", "daemon", "--offline"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait for daemon to start
        import time
        time.sleep(2)

        # Get API port from config
        config_path = self.repo_dir / "config"
        config = json.loads(config_path.read_text())
        api_addr = config["Addresses"]["API"]
        self.api_port = api_addr.split("/")[-1]

        log(f"Temp IPFS daemon started on port {self.api_port}")
        return f"http://127.0.0.1:{self.api_port}/api/v0"

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Stop daemon
        if self.daemon_process:
            self.daemon_process.terminate()
            self.daemon_process.wait(timeout=5)

        # Clean up temp directory
        if self.repo_dir and self.repo_dir.exists():
            shutil.rmtree(self.repo_dir)
            log("Cleaned up temp repo")

def import_car(car_path: Path, ipfs_api: str):
    """Import CAR file to IPFS repo."""
    log(f"Importing CAR: {car_path.name}")

    result = subprocess.run(
        ["ipfs", "dag", "import", "--pin-roots=true", str(car_path)],
        env={"IPFS_API": ipfs_api},
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        fail(f"CAR import failed: {result.stderr}")

    # Parse import stats
    if "imported" in result.stderr:
        log(f"  {result.stderr.strip()}")

    success("CAR imported successfully")

def dag_get(cid: str, ipfs_api: str) -> Dict[str, Any]:
    """Fetch DAG object from IPFS."""
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{ipfs_api}/dag/get",
            params={"arg": cid}
        )
        response.raise_for_status()
        return response.json()

def verify_cid_exists(cid: str, ipfs_api: str) -> bool:
    """Check if CID exists in repo."""
    try:
        dag_get(cid, ipfs_api)
        return True
    except:
        return False

def verify_manifest_chain(tip_cid: str, ipfs_api: str, results: Dict[str, Any]):
    """Walk manifest version chain and verify all versions exist."""
    current = tip_cid
    version_count = 0

    while current:
        if not verify_cid_exists(current, ipfs_api):
            results["missing_cids"].append(current)
            warn(f"Missing manifest: {current[:16]}")
            return

        version_count += 1
        manifest = dag_get(current, ipfs_api)

        # Verify components
        components = manifest.get("components", {})
        for comp_name, comp_cid_obj in components.items():
            comp_cid = comp_cid_obj.get("/") if isinstance(comp_cid_obj, dict) else comp_cid_obj

            if not verify_cid_exists(comp_cid, ipfs_api):
                results["missing_cids"].append(comp_cid)
                warn(f"Missing component {comp_name}: {comp_cid[:16]}")
            else:
                results["component_count"] += 1

        # Move to previous
        prev = manifest.get("prev")
        if prev:
            current = prev.get("/") if isinstance(prev, dict) else prev
        else:
            break

    results["manifest_count"] += version_count

def verify_event_chain(event_head: str, ipfs_api: str, results: Dict[str, Any]):
    """Walk event chain and verify all entries exist."""
    current = event_head
    event_count = 0

    while current:
        if not verify_cid_exists(current, ipfs_api):
            results["missing_cids"].append(current)
            warn(f"Missing event: {current[:16]}")
            return

        event_count += 1
        event = dag_get(current, ipfs_api)

        # Move to previous
        prev = event.get("prev")
        if prev:
            current = prev.get("/") if isinstance(prev, dict) else prev
        else:
            break

    results["event_count"] = event_count

def verify_car_contents(car_path: Path, snapshot_cid: str):
    """Verify CAR contains all expected content."""
    log("Starting CAR verification...")

    results = {
        "manifest_count": 0,
        "component_count": 0,
        "event_count": 0,
        "missing_cids": []
    }

    with TempIPFSRepo() as ipfs_api:
        # Import CAR
        import_car(car_path, ipfs_api)

        # Verify snapshot exists
        log("Verifying snapshot object...")
        if not verify_cid_exists(snapshot_cid, ipfs_api):
            fail(f"Snapshot CID not found: {snapshot_cid}")

        snapshot = dag_get(snapshot_cid, ipfs_api)
        success(f"Snapshot found: {len(snapshot['entries'])} entries")

        # Verify each entry
        entries = snapshot["entries"]
        log(f"Verifying {len(entries)} entries...")

        for i, entry in enumerate(entries):
            if (i + 1) % 100 == 0:
                log(f"  Verified {i + 1}/{len(entries)} entries...")

            tip_cid = entry["tip_cid"].get("/") if isinstance(entry["tip_cid"], dict) else entry["tip_cid"]

            # Verify manifest chain (includes components)
            verify_manifest_chain(tip_cid, ipfs_api, results)

        success(f"All {len(entries)} entries verified")

        # Verify event chain
        if "event_cid" in snapshot:
            log("Verifying event chain...")
            verify_event_chain(snapshot["event_cid"], ipfs_api, results)
            success(f"Event chain verified: {results['event_count']} events")

    # Check for missing CIDs
    if results["missing_cids"]:
        fail(f"CAR is incomplete: {len(results['missing_cids'])} missing CIDs")

    return results

def main():
    if len(sys.argv) < 2:
        print("Usage: verify-car.py <car-file> [snapshot-cid]", file=sys.stderr)
        print("", file=sys.stderr)
        print("If snapshot-cid is not provided, will read from metadata file", file=sys.stderr)
        sys.exit(1)

    car_path = Path(sys.argv[1])
    if not car_path.exists():
        fail(f"CAR file not found: {car_path}")

    # Get snapshot CID
    if len(sys.argv) > 2:
        snapshot_cid = sys.argv[2]
    else:
        # Try to read from metadata
        metadata_path = car_path.with_suffix(".json")
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            snapshot_cid = metadata.get("snapshot_cid")
        else:
            fail("Snapshot CID not provided and no metadata file found")

    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("CAR File Verification", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"File:     {car_path.name}", file=sys.stderr)
    print(f"Snapshot: {snapshot_cid}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Verify CAR
    results = verify_car_contents(car_path, snapshot_cid)

    # Summary
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"{GREEN}CAR Verification Complete{NC}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Manifests:  {results['manifest_count']}", file=sys.stderr)
    print(f"Components: {results['component_count']}", file=sys.stderr)
    print(f"Events:     {results['event_count']}", file=sys.stderr)
    print(f"Status:     ✓ All content verified", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Output JSON for scripting
    print(json.dumps({
        "car_file": str(car_path),
        "snapshot_cid": snapshot_cid,
        "manifest_count": results["manifest_count"],
        "component_count": results["component_count"],
        "event_count": results["event_count"],
        "verified": True
    }))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        fail(f"Verification failed: {e}")
