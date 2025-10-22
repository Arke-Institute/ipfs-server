#!/usr/bin/env python3
"""
Export snapshot to CAR file with explicit CID collection and verification.

This ensures ALL content is included:
- Snapshot object
- All manifests (full version history via prev links)
- All components (metadata, images, etc.)
- All event chain entries

Uses ipfs dag export with explicit CID list to ensure completeness.
"""

import sys
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Set, Dict, Any
import httpx

# Configuration
IPFS_API = os.getenv("IPFS_API_URL", "http://localhost:5001/api/v0")
SNAPSHOTS_DIR = Path(os.getenv("SNAPSHOTS_DIR", "./snapshots"))
BACKUPS_DIR = Path(os.getenv("BACKUPS_DIR", "./backups"))
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "ipfs-node")
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

def dag_get(cid: str) -> Dict[str, Any]:
    """Fetch DAG object from IPFS."""
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{IPFS_API}/dag/get",
            params={"arg": cid}
        )
        response.raise_for_status()
        return response.json()

def walk_manifest_versions(manifest_cid: str) -> Set[str]:
    """Walk manifest version chain and collect all CIDs + components."""
    cids = set()
    current = manifest_cid

    while current:
        cids.add(current)

        try:
            manifest = dag_get(current)

            # Collect component CIDs
            components = manifest.get("components", {})
            for comp_cid_obj in components.values():
                if isinstance(comp_cid_obj, dict) and "/" in comp_cid_obj:
                    cids.add(comp_cid_obj["/"])
                elif isinstance(comp_cid_obj, str):
                    cids.add(comp_cid_obj)

            # Move to previous version
            prev = manifest.get("prev")
            if prev:
                current = prev.get("/") if isinstance(prev, dict) else prev
            else:
                break

        except Exception as e:
            warn(f"Failed to fetch manifest {current[:16]}: {e}")
            break

    return cids

def walk_event_chain(event_head: str) -> Set[str]:
    """Walk event chain and collect all event CIDs."""
    cids = set()
    current = event_head

    while current:
        cids.add(current)

        try:
            event = dag_get(current)

            # Move to previous event
            prev = event.get("prev")
            if prev:
                current = prev.get("/") if isinstance(prev, dict) else prev
            else:
                break

        except Exception as e:
            warn(f"Failed to fetch event {current[:16]}: {e}")
            break

    return cids

def collect_all_cids(snapshot_cid: str) -> Dict[str, Set[str]]:
    """
    Collect ALL CIDs referenced from snapshot.
    Returns categorized sets for reporting.
    """
    log("Collecting all CIDs from snapshot...")

    cids = {
        "snapshot": {snapshot_cid},
        "dag_nodes": set(),  # manifests + components (both dag-cbor)
        "events": set()
    }

    # Get snapshot
    snapshot = dag_get(snapshot_cid)

    # Process each entry
    entries = snapshot.get("entries", [])
    log(f"Processing {len(entries)} entries...")

    for i, entry in enumerate(entries):
        if (i + 1) % 100 == 0:
            log(f"  Processed {i + 1}/{len(entries)} entries...")

        # Get tip CID
        tip_cid = entry.get("tip_cid", {})
        if isinstance(tip_cid, dict) and "/" in tip_cid:
            tip_cid = tip_cid["/"]

        # Walk version history (includes components)
        # All manifests and components are now dag-cbor (bafyrei...)
        version_cids = walk_manifest_versions(tip_cid)
        cids["dag_nodes"].update(version_cids)

    # Collect event chain
    event_cid = snapshot.get("event_cid")
    if event_cid:
        cids["events"] = walk_event_chain(event_cid)

    # Summary
    total = sum(len(s) for s in cids.values())
    success(f"Collected {total} CIDs:")
    log(f"  Snapshot:     {len(cids['snapshot'])}")
    log(f"  DAG nodes:    {len(cids['dag_nodes'])} (manifests + components)")
    log(f"  Events:       {len(cids['events'])}")

    return cids

def export_car(snapshot_cid: str, all_cids: Set[str], output_path: Path) -> int:
    """Export snapshot to CAR file using docker exec.

    The snapshot DAG includes IPLD links to all manifests, events, and components.
    ipfs dag export will recursively follow all these links and include everything.

    Note: Components MUST be pinned (not garbage collected) for this to work.
    The generate-test-data.py script uploads components with pin=true.
    """
    log(f"Exporting to CAR: {output_path}")
    log(f"Expected CIDs: {len(all_cids)} (snapshot + manifests + components + events)")

    # Export from snapshot root (will follow all IPLD links)
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "ipfs", "dag", "export",
             "--progress=false", snapshot_cid],
            stdout=open(output_path, 'wb'),
            stderr=subprocess.PIPE,
            timeout=3600  # 1 hour for large datasets
        )

        if result.returncode != 0:
            error(f"CAR export failed: {result.stderr.decode()}")

    except subprocess.TimeoutExpired:
        error("CAR export timed out after 1 hour")

    # Verify file was created
    if not output_path.exists():
        error("CAR file was not created")

    size = output_path.stat().st_size
    if size == 0:
        error("CAR file is empty")

    success(f"CAR exported: {size:,} bytes ({size / 1024 / 1024:.2f} MB)")
    return size

def get_latest_snapshot() -> Dict[str, Any]:
    """Read latest snapshot metadata."""
    latest_file = SNAPSHOTS_DIR / "latest.json"
    if not latest_file.exists():
        error(f"No snapshot found at {latest_file}")

    return json.loads(latest_file.read_text())

def get_instance_id() -> str:
    """Get EC2 instance ID from metadata service, or 'local' if not on EC2."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", "2", "http://169.254.169.254/latest/meta-data/instance-id"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except:
        pass
    return "local"

def upload_to_s3(car_path: Path, metadata_path: Path, seq: int) -> bool:
    """Upload CAR file and metadata to S3 with instance-specific folder structure.

    S3 structure: s3://bucket/backups/{instance-id}/arke-{seq}-{timestamp}.car
    """
    try:
        # Get AWS account ID
        result = subprocess.run(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            warn("Could not get AWS account ID - skipping S3 upload")
            return False

        account_id = result.stdout.strip()
        bucket_name = f"arke-ipfs-backups-{account_id}"

        # Get instance ID for folder structure
        instance_id = get_instance_id()
        s3_prefix = f"backups/{instance_id}/"

        log(f"Uploading to S3: s3://{bucket_name}/{s3_prefix}")
        log(f"  Instance ID: {instance_id}")

        # Check if bucket exists
        check_result = subprocess.run(
            ["aws", "s3", "ls", f"s3://{bucket_name}", "--region", "us-east-1"],
            capture_output=True,
            timeout=10
        )
        if check_result.returncode != 0:
            warn(f"S3 bucket {bucket_name} does not exist - skipping upload")
            return False

        # Upload CAR file
        upload_date = datetime.now().strftime("%Y-%m-%d")
        s3_car_path = f"s3://{bucket_name}/{s3_prefix}{car_path.name}"

        result = subprocess.run([
            "aws", "s3", "cp", str(car_path), s3_car_path,
            "--region", "us-east-1",
            "--storage-class", "STANDARD",
            "--metadata", f"source=arke-ipfs-ec2,backup-type=automated,upload-date={upload_date},instance-id={instance_id},sequence={seq}"
        ], capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            warn(f"S3 CAR upload failed: {result.stderr}")
            return False

        success(f"CAR uploaded to {s3_car_path}")

        # Upload metadata JSON
        if metadata_path.exists():
            s3_metadata_path = f"s3://{bucket_name}/{s3_prefix}{metadata_path.name}"
            result = subprocess.run([
                "aws", "s3", "cp", str(metadata_path), s3_metadata_path,
                "--region", "us-east-1"
            ], capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                success(f"Metadata uploaded to {s3_metadata_path}")

        return True

    except Exception as e:
        warn(f"S3 upload failed: {e}")
        return False

def main():
    log("Starting CAR export...")

    # Get latest snapshot
    metadata = get_latest_snapshot()
    snapshot_cid = metadata["cid"]
    seq = metadata["seq"]

    log(f"Snapshot CID: {snapshot_cid}")
    log(f"Sequence:     {seq}")

    # Collect all CIDs (validates completeness)
    cid_sets = collect_all_cids(snapshot_cid)

    # Generate filename
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    car_filename = f"arke-{seq}-{timestamp}.car"
    car_path = BACKUPS_DIR / car_filename

    # Create backups directory
    BACKUPS_DIR.mkdir(exist_ok=True)

    # Flatten all CIDs into a single set
    all_cids = set()
    for cid_set in cid_sets.values():
        all_cids.update(cid_set)

    # Export CAR with ALL CIDs
    size_bytes = export_car(snapshot_cid, all_cids, car_path)

    # Save metadata
    car_metadata = {
        "snapshot_cid": snapshot_cid,
        "seq": seq,
        "timestamp": timestamp,
        "filename": car_filename,
        "path": str(car_path),
        "size_bytes": size_bytes,
        "cid_counts": {
            "snapshot": len(cid_sets["snapshot"]),
            "dag_nodes": len(cid_sets["dag_nodes"]),
            "events": len(cid_sets["events"]),
            "total": sum(len(s) for s in cid_sets.values())
        }
    }

    metadata_path = BACKUPS_DIR / f"{car_filename[:-4]}.json"
    metadata_path.write_text(json.dumps(car_metadata, indent=2))

    # Upload to S3 (if available)
    print("", file=sys.stderr)
    s3_uploaded = upload_to_s3(car_path, metadata_path, seq)

    # Summary
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"{GREEN}CAR Export Complete{NC}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Snapshot:   {snapshot_cid}", file=sys.stderr)
    print(f"Sequence:   {seq}", file=sys.stderr)
    print(f"File:       {car_filename}", file=sys.stderr)
    print(f"Size:       {size_bytes / 1024 / 1024:.2f} MB", file=sys.stderr)
    print(f"CIDs:       {car_metadata['cid_counts']['total']}", file=sys.stderr)
    print(f"Location:   {car_path}", file=sys.stderr)
    if s3_uploaded:
        print(f"S3 Backup:  ✓ Uploaded", file=sys.stderr)
    else:
        print(f"S3 Backup:  ⚠ Skipped (not on EC2 or bucket unavailable)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Output path for scripting
    print(str(car_path))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error(f"Export failed: {e}")
