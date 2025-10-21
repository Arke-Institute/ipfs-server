#!/usr/bin/env python3
"""
Generate controlled test data for DR testing.

Creates 3 entities with version history:
- Entity A: v1 (create) → v2 (update metadata) → v3 (add image)
- Entity B: v1 (create) → v2 (update metadata)
- Entity C: v1 (create only)

Event chain (newest → oldest):
[update-A-v3] → [update-A-v2] → [update-B-v2] → [create-C] → [create-B] → [create-A]
"""

import sys
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import httpx
import subprocess

# Configuration
IPFS_API = os.getenv("IPFS_API_URL", "http://localhost:5001/api/v0")
INDEX_POINTER_PATH = os.getenv("INDEX_POINTER_PATH", "/arke/index-pointer")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "ipfs-node")
TIMEOUT = 30.0

# Test entity PIs (sharded to EN/TI directory)
ENTITY_A = "ENTITY_A00000000000000"
ENTITY_B = "ENTITY_B00000000000000"
ENTITY_C = "ENTITY_C00000000000000"

# Colors
BLUE = '\033[0;34m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
NC = '\033[0m'

def log(msg: str):
    print(f"{BLUE}[INFO]{NC} {msg}", file=sys.stderr)

def success(msg: str):
    print(f"{GREEN}[SUCCESS]{NC} {msg}", file=sys.stderr)

def upload_content(content: bytes, filename: str) -> str:
    """Upload content wrapped in DAG-CBOR to IPFS and return CID.

    IMPORTANT: We wrap raw content in a DAG-CBOR node so that ipfs dag export
    will include it in CAR files. Raw blocks are NOT traversed by dag export.
    """
    log(f"Uploading {filename} ({len(content)} bytes)...")

    # Wrap content in DAG-CBOR structure
    # For binary data (images), encode as base64 string
    # For text data (JSON), decode to string
    try:
        content_str = content.decode('utf-8')
    except UnicodeDecodeError:
        # Binary data - use base64
        import base64
        content_str = base64.b64encode(content).decode('ascii')

    dag_node = {
        "data": content_str,
        "filename": filename
    }

    # Store as DAG-CBOR using CLI (same as manifests)
    dag_json = json.dumps(dag_node)

    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER_NAME, "ipfs", "dag", "put",
         "--store-codec=dag-cbor", "--input-codec=dag-json", "--pin=true"],
        input=dag_json,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise Exception(f"ipfs dag put failed: {result.stderr}")

    cid = result.stdout.strip()
    success(f"  → {cid} (DAG-CBOR)")
    return cid

def store_manifest(manifest: dict) -> str:
    """Store manifest as DAG-CBOR using ipfs CLI via docker exec.

    CRITICAL: Use --input-codec=dag-json (not json) to ensure IPLD links
    like {"/": "cid"} are properly encoded as CBOR tag-42 links, not plain maps.
    Without this, ipfs dag export won't traverse component links!
    """
    log(f"Storing manifest for {manifest['pi']} v{manifest['ver']}...")

    manifest_json = json.dumps(manifest)

    # Use docker exec to run ipfs dag put
    # CRITICAL: --input-codec=dag-json converts {"/": "cid"} to real IPLD links (tag-42)
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER_NAME, "ipfs", "dag", "put",
         "--store-codec=dag-cbor", "--input-codec=dag-json", "--pin=true"],
        input=manifest_json,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise Exception(f"ipfs dag put failed: {result.stderr}")

    # Parse CID from output (plain CID string)
    manifest_cid = result.stdout.strip()
    success(f"  → {manifest_cid}")
    return manifest_cid

def write_tip_file(pi: str, cid: str):
    """Write .tip file to MFS."""
    shard1 = pi[:2]
    shard2 = pi[2:4]
    dir_path = f"/arke/index/{shard1}/{shard2}"
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
            files={"file": ("tip", cid.encode())}
        )
        response.raise_for_status()

    log(f"  Wrote .tip file: {tip_path}")

def append_event(event_type: str, pi: str, ver: int, tip_cid: str, prev_event_cid: str = None) -> str:
    """Append event to chain using dag-json via docker exec."""
    log(f"Appending {event_type} event for {pi} v{ver}...")

    event = {
        "schema": "arke/event@v1",
        "type": event_type,
        "pi": pi,
        "ver": ver,
        "tip_cid": {"/": tip_cid},
        "ts": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "prev": {"/": prev_event_cid} if prev_event_cid else None
    }

    event_json = json.dumps(event)

    # Use docker exec to run ipfs dag put
    result = subprocess.run(
        ["docker", "exec", "-i", CONTAINER_NAME, "ipfs", "dag", "put",
         "--store-codec=dag-json", "--input-codec=json", "--pin=true"],
        input=event_json,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise Exception(f"ipfs dag put failed: {result.stderr}")

    # Parse CID from output (plain CID string)
    event_cid = result.stdout.strip()
    success(f"  → Event CID: {event_cid}")
    return event_cid

def update_index_pointer(event_head: str, event_count: int, total_count: int):
    """Update index pointer with event head."""
    log("Updating index pointer...")

    pointer = {
        "schema": "arke/index-pointer@v2",
        "event_head": event_head,
        "event_count": event_count,
        "total_count": total_count,
        "last_updated": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }

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

    success("Index pointer updated")

def create_entity_version(pi: str, ver: int, prev_cid: str = None, metadata_content: dict = None, image_content: bytes = None, prev_event: str = None):
    """Create a single version of an entity."""
    timestamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    # Upload components
    components = {}

    if metadata_content:
        metadata_json = json.dumps(metadata_content, indent=2).encode()
        metadata_cid = upload_content(metadata_json, f"{pi}_metadata_v{ver}.json")
        components["metadata"] = {"/": metadata_cid}

    if image_content:
        image_cid = upload_content(image_content, f"{pi}_image_v{ver}.png")
        components["image"] = {"/": image_cid}

    # Create manifest
    manifest = {
        "schema": "arke/manifest/v1",
        "pi": pi,
        "ver": ver,
        "ts": timestamp,
        "prev": {"/": prev_cid} if prev_cid else None,
        "components": components,
        "children_pi": [],
        "note": f"Version {ver}"
    }

    manifest_cid = store_manifest(manifest)

    # Write .tip file
    write_tip_file(pi, manifest_cid)

    # Append event
    event_type = "create" if ver == 1 else "update"
    event_cid = append_event(event_type, pi, ver, manifest_cid, prev_event)

    return manifest_cid, event_cid

def generate_test_dataset():
    """Generate complete test dataset."""
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Generating Test Dataset", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    event_chain = []

    # Entity A - Version 1 (create)
    log("\n[1/6] Creating Entity A v1...")
    metadata_a1 = {"name": "Entity A", "description": "Initial version"}
    manifest_a1, event_a1 = create_entity_version(
        ENTITY_A, 1,
        metadata_content=metadata_a1
    )
    event_chain.append(event_a1)

    # Entity B - Version 1 (create)
    log("\n[2/6] Creating Entity B v1...")
    metadata_b1 = {"name": "Entity B", "description": "Initial version"}
    manifest_b1, event_b1 = create_entity_version(
        ENTITY_B, 1,
        metadata_content=metadata_b1,
        prev_event=event_a1
    )
    event_chain.append(event_b1)

    # Entity C - Version 1 (create)
    log("\n[3/6] Creating Entity C v1...")
    metadata_c1 = {"name": "Entity C", "description": "Single version entity"}
    manifest_c1, event_c1 = create_entity_version(
        ENTITY_C, 1,
        metadata_content=metadata_c1,
        prev_event=event_b1
    )
    event_chain.append(event_c1)

    # Entity B - Version 2 (update metadata)
    log("\n[4/6] Creating Entity B v2...")
    metadata_b2 = {"name": "Entity B Updated", "description": "Metadata change"}
    manifest_b2, event_b2 = create_entity_version(
        ENTITY_B, 2,
        prev_cid=manifest_b1,
        metadata_content=metadata_b2,
        prev_event=event_c1
    )
    event_chain.append(event_b2)

    # Entity A - Version 2 (update metadata)
    log("\n[5/6] Creating Entity A v2...")
    metadata_a2 = {"name": "Entity A Updated", "description": "Metadata change"}
    manifest_a2, event_a2 = create_entity_version(
        ENTITY_A, 2,
        prev_cid=manifest_a1,
        metadata_content=metadata_a2,
        prev_event=event_b2
    )
    event_chain.append(event_a2)

    # Entity A - Version 3 (add image)
    log("\n[6/6] Creating Entity A v3 with image...")
    metadata_a3 = {"name": "Entity A Final", "description": "With image"}
    # Create a simple 1x1 PNG (smallest valid PNG)
    image_data = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000 1f15c4890000000a49444154789c6200010000050001 0d0a2db40000000049454e44ae426082')
    manifest_a3, event_a3 = create_entity_version(
        ENTITY_A, 3,
        prev_cid=manifest_a2,
        metadata_content=metadata_a3,
        image_content=image_data,
        prev_event=event_a2
    )
    event_chain.append(event_a3)

    # Update index pointer
    update_index_pointer(event_a3, 6, 3)

    # Summary
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"{GREEN}Test Data Generation Complete{NC}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Entities Created: 3 ({ENTITY_A}, {ENTITY_B}, {ENTITY_C})", file=sys.stderr)
    print(f"Total Versions:   6 (A=3, B=2, C=1)", file=sys.stderr)
    print(f"Event Chain Head: {event_a3}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # Output for scripting
    print(json.dumps({
        "entities": [ENTITY_A, ENTITY_B, ENTITY_C],
        "event_head": event_a3,
        "event_count": 6,
        "total_count": 3
    }))

if __name__ == "__main__":
    try:
        generate_test_dataset()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
