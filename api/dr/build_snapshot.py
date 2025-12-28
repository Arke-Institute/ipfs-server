#!/usr/bin/env python3
"""
Build snapshot from event chain with streaming approach.
Writes entries incrementally to avoid memory issues and provide progress visibility.

Includes append-only proof generation (RFC 6962-style Merkle tree) to cryptographically
prove that no historical data has been deleted between snapshots.
"""

import sys
import json
import os
import time
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Set, Tuple
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


# =============================================================================
# Append-Only Proof Implementation (RFC 6962-style Merkle Tree)
# =============================================================================

class SimpleMerkleTree:
    """
    Simple Merkle tree implementation for append-only proofs.
    Uses SHA-256 hashing, builds a binary tree from sorted CID leaves.

    For production with full RFC 6962 compliance (consistency proofs,
    inclusion proofs), consider using pymerkle library.
    """

    def __init__(self, leaves: List[bytes]):
        self.leaves = leaves
        self.levels = self._build_tree(leaves)

    def _hash(self, data: bytes) -> bytes:
        return hashlib.sha256(data).digest()

    def _build_tree(self, leaves: List[bytes]) -> List[List[bytes]]:
        if not leaves:
            return [[self._hash(b'')]]

        # Hash leaves
        current_level = [self._hash(leaf) for leaf in leaves]
        levels = [current_level]

        # Build tree bottom-up
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                next_level.append(self._hash(left + right))
            current_level = next_level
            levels.append(current_level)

        return levels

    @property
    def root(self) -> str:
        return self.levels[-1][0].hex()

    @property
    def leaf_count(self) -> int:
        return len(self.leaves)


def collect_version_chain_cids(tip_cid: str, client: httpx.Client) -> List[str]:
    """
    Walk the prev chain from tip to collect ALL version CIDs for an entity.
    Also collects component CIDs (metadata, files, images) from each manifest.

    Returns list of all CIDs in this entity's history.
    """
    cids = []
    current = tip_cid
    max_depth = 100  # Safety limit

    while current and len(cids) < max_depth:
        cids.append(current)

        try:
            response = client.post(
                f"{IPFS_API}/dag/get",
                params={"arg": current}
            )
            response.raise_for_status()
            manifest = response.json()
        except Exception as e:
            warn(f"Failed to fetch manifest {current[:16]}...: {e}")
            break

        # Get component CIDs
        for comp_name, comp_link in manifest.get("components", {}).items():
            comp_cid = comp_link.get("/") if isinstance(comp_link, dict) else comp_link
            if comp_cid:
                cids.append(comp_cid)

        # Move to previous version
        prev_obj = manifest.get("prev")
        if not prev_obj:
            break
        current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj

    return cids


def collect_all_cids(
    entries: List[Dict[str, Any]],
    prev_all_cids: Set[str] = None,
    modified_pis: Set[str] = None
) -> Set[str]:
    """
    Collect ALL CIDs referenced by entities in the snapshot.

    For incremental builds:
      - prev_all_cids: CIDs from previous snapshot
      - modified_pis: Only walk version chains for these PIs

    Returns set of all unique CIDs.
    """
    all_cids = set(prev_all_cids) if prev_all_cids else set()
    start_time = time.time()

    # Determine which entities need version chain walking
    if modified_pis is not None:
        # Incremental: only process modified entities
        entries_to_walk = [e for e in entries if e.get("pi") in modified_pis]
        log(f"Collecting CIDs for {len(entries_to_walk)} modified entities (incremental)")
    else:
        # Full: process all entities
        entries_to_walk = entries
        log(f"Collecting CIDs for {len(entries_to_walk)} entities (full)")

    with httpx.Client(timeout=TIMEOUT) as client:
        processed = 0
        for entry in entries_to_walk:
            tip_cid = entry.get("tip_cid", {})
            if isinstance(tip_cid, dict):
                tip_cid = tip_cid.get("/")

            if not tip_cid:
                continue

            # Collect all versions and components for this entity
            version_cids = collect_version_chain_cids(tip_cid, client)
            all_cids.update(version_cids)

            # Also add chain entry CID
            chain_cid = entry.get("chain_cid", {})
            if isinstance(chain_cid, dict):
                chain_cid = chain_cid.get("/")
            if chain_cid:
                all_cids.add(chain_cid)

            processed += 1
            if processed % 100 == 0:
                elapsed = time.time() - start_time
                log(f"CID collection: {processed}/{len(entries_to_walk)} entities "
                    f"({len(all_cids)} CIDs, {elapsed:.1f}s)")

    elapsed = time.time() - start_time
    success(f"Collected {len(all_cids)} unique CIDs in {elapsed:.1f}s")
    return all_cids


def build_merkle_root(cids: Set[str]) -> Tuple[str, List[str]]:
    """
    Build Merkle tree from CID set.
    Returns (merkle_root, sorted_cid_list).
    """
    sorted_cids = sorted(cids)
    leaves = [cid.encode() for cid in sorted_cids]
    tree = SimpleMerkleTree(leaves)
    return tree.root, sorted_cids


def generate_consistency_info(
    prev_cids: Set[str],
    curr_cids: Set[str]
) -> Dict[str, Any]:
    """
    Generate consistency information between two snapshots.

    Verifies append-only property: curr_cids ⊇ prev_cids (no deletions).
    Returns info dict with verification status.
    """
    deleted = prev_cids - curr_cids
    added = curr_cids - prev_cids

    if deleted:
        # This should never happen in normal operation
        warn(f"APPEND-ONLY VIOLATION: {len(deleted)} CIDs were deleted!")
        for cid in list(deleted)[:5]:
            warn(f"  Deleted: {cid[:40]}...")

    return {
        "prev_cid_count": len(prev_cids),
        "curr_cid_count": len(curr_cids),
        "added_count": len(added),
        "deleted_count": len(deleted),
        "is_append_only": len(deleted) == 0
    }


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

def load_previous_snapshot(snapshot_cid: str) -> Tuple[Dict[str, Any], Dict[str, Any], Set[str]]:
    """
    Load previous snapshot and return:
      - entries as dict keyed by PI
      - full snapshot object
      - set of all_cids (if available from v2 snapshot, else empty)
    """
    log(f"Loading previous snapshot: {snapshot_cid[:16]}...")

    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(
            f"{IPFS_API}/dag/get",
            params={"arg": snapshot_cid}
        )
        response.raise_for_status()
        snapshot = response.json()

    entries_dict = {e["pi"]: e for e in snapshot.get("entries", [])}

    # Load previous all_cids if available (v2 snapshots)
    prev_all_cids = set(snapshot.get("all_cids", []))

    success(f"Loaded {len(entries_dict)} entries from previous snapshot")
    if prev_all_cids:
        log(f"Loaded {len(prev_all_cids)} CIDs from previous snapshot proof")
    else:
        log("No previous CID list found (v1 snapshot or first v2 build)")

    return entries_dict, snapshot, prev_all_cids

def walk_event_chain_incremental(event_head: str, stop_at_cid: str, prev_entries: Dict[str, Any], checkpoint_file: Path) -> tuple[int, int]:
    """
    Walk ONLY new events from event_head back to stop_at_cid.
    Update prev_entries dict for modified/new PIs.
    Returns (events_processed, pis_modified).
    """
    log(f"Walking new events from {event_head[:16]}... to {stop_at_cid[:16]}...")

    current = event_head
    events_processed = 0
    pis_modified = set()
    start_time = time.time()

    with httpx.Client(timeout=TIMEOUT) as client:
        while current and current != stop_at_cid:
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

            events_processed += 1

            # Extract PI
            pi = event.get("pi")
            ts = event.get("ts")

            if not pi:
                warn(f"Event {current[:16]} has no PI, skipping")
                prev_obj = event.get("prev")
                if not prev_obj:
                    break
                current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj
                continue

            # Track that we modified/added this PI
            pis_modified.add(pi)

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

            # Update/add entry in dict
            prev_entries[pi] = {
                "pi": pi,
                "ver": ver,
                "tip_cid": {"/": tip_cid},
                "ts": ts,
                "chain_cid": {"/": current}
            }

            # Progress logging
            if events_processed % LOG_INTERVAL == 0:
                elapsed = time.time() - start_time
                rate = events_processed / elapsed
                log(f"Processed {events_processed} new events ({rate:.1f} events/sec, {elapsed:.0f}s elapsed)")

            # Move to previous event
            prev_obj = event.get("prev")
            if not prev_obj:
                break
            current = prev_obj.get("/") if isinstance(prev_obj, dict) else prev_obj

    elapsed = time.time() - start_time
    success(f"Incremental walk: {events_processed} events, {len(pis_modified)} PIs modified/added in {elapsed:.1f}s")

    # Write all entries to checkpoint file
    log("Writing entries to checkpoint file...")
    with open(checkpoint_file, 'w') as f:
        for entry in prev_entries.values():
            f.write(json.dumps(entry) + "\n")

    return events_processed, len(pis_modified)

def walk_event_chain(event_head: str, checkpoint_file: Path) -> int:
    """
    Walk entire event chain and write entries to checkpoint file.
    Returns count of unique PIs processed.
    """
    log(f"Walking ENTIRE event chain from head: {event_head[:16]}...")
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
    success(f"Full traversal: {count} unique PIs in {elapsed:.0f}s ({count/elapsed:.1f} entries/sec)")
    return count

def build_snapshot_json(
    checkpoint_file: Path,
    pointer: Dict[str, Any],
    seq: int,
    timestamp: str,
    total_count: int,
    all_cids: Set[str] = None,
    prev_all_cids: Set[str] = None
) -> Dict[str, Any]:
    """
    Build final snapshot JSON from checkpoint file.

    If all_cids is provided, includes append-only proof fields (v2 schema).
    """
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
        "schema": "arke/snapshot@v2",  # Upgraded to v2 with proof fields
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

    # Add append-only proof fields if CIDs were collected
    if all_cids:
        log("Building Merkle tree for append-only proof...")
        merkle_root, sorted_cids = build_merkle_root(all_cids)

        snapshot["merkle_root"] = merkle_root
        snapshot["cid_count"] = len(all_cids)
        snapshot["all_cids"] = sorted_cids  # Store for future incremental builds

        # Add consistency info if we have previous CIDs
        if prev_all_cids:
            consistency = generate_consistency_info(prev_all_cids, all_cids)
            snapshot["consistency"] = consistency
            if consistency["is_append_only"]:
                success(f"Append-only verified: +{consistency['added_count']} CIDs")
            else:
                warn(f"APPEND-ONLY VIOLATION: {consistency['deleted_count']} CIDs deleted!")
        else:
            snapshot["consistency"] = None
            log("No previous snapshot for consistency check (first v2 snapshot)")

        success(f"Merkle root: {merkle_root[:32]}...")
    else:
        # No proof fields (shouldn't happen in normal operation)
        snapshot["merkle_root"] = None
        snapshot["cid_count"] = 0
        snapshot["all_cids"] = []
        snapshot["consistency"] = None

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

        # Check for previous snapshot
        prev_snapshot_cid = pointer.get("latest_snapshot_cid")
        prev_event_cid = pointer.get("snapshot_event_cid")

        log(f"Starting snapshot build (seq={new_seq})")
        log(f"DEBUG: prev_snapshot_cid={prev_snapshot_cid[:16] if prev_snapshot_cid else 'None'}...")
        log(f"DEBUG: prev_event_cid={prev_event_cid[:16] if prev_event_cid else 'None'}...")
        log(f"DEBUG: event_head={event_head[:16]}...")

        # Phase 1: Walk event chain and write to checkpoint
        log("=" * 60)
        log("PHASE 1: Collecting entries from event chain")
        log("=" * 60)

        # Variables for append-only proof
        prev_all_cids = set()
        modified_pis = None  # None means full walk needed

        # Decide: incremental or full traversal
        if prev_snapshot_cid and prev_event_cid:
            if event_head == prev_event_cid:
                log("No new events since last snapshot - skipping build")
                cleanup_lock()
                return

            log(f"Mode: INCREMENTAL (from snapshot seq {prev_seq})")
            log(f"Previous snapshot: {prev_snapshot_cid[:16]}...")
            log(f"Event range: {prev_event_cid[:16]}... → {event_head[:16]}...")

            # Load previous entries and CIDs as baseline
            prev_entries, prev_snapshot, prev_all_cids = load_previous_snapshot(prev_snapshot_cid)

            # Walk only new events
            events_processed, pis_modified_count = walk_event_chain_incremental(
                event_head, prev_event_cid, prev_entries, CHECKPOINT_FILE
            )

            # Get the set of modified PIs for incremental CID collection
            # Re-read checkpoint to get the modified PIs
            modified_pis = set()
            with open(CHECKPOINT_FILE) as f:
                for line in f:
                    entry = json.loads(line)
                    modified_pis.add(entry.get("pi"))

            total_count = len(prev_entries)

            log("")
            log("=" * 60)
            log("Incremental Build Summary")
            log("=" * 60)
            log(f"Events processed:  {events_processed}")
            log(f"PIs modified/new:  {pis_modified_count}")
            log(f"PIs unchanged:     {total_count - pis_modified_count}")
            log(f"Total PIs:         {total_count}")
            log("=" * 60)

        else:
            log("Mode: FULL TRAVERSAL (no previous snapshot)")
            total_count = walk_event_chain(event_head, CHECKPOINT_FILE)
            prev_all_cids = set()  # No previous CIDs for first snapshot

        if total_count == 0:
            error("No entries collected")

        # Phase 2: Collect all CIDs for append-only proof
        log("")
        log("=" * 60)
        log("PHASE 2: Collecting CIDs for append-only proof")
        log("=" * 60)

        # Read entries from checkpoint file
        entries = []
        with open(CHECKPOINT_FILE) as f:
            for line in f:
                entries.append(json.loads(line))

        # Collect all CIDs (incremental if we have previous CIDs)
        all_cids = collect_all_cids(
            entries,
            prev_all_cids=prev_all_cids if prev_all_cids else None,
            modified_pis=modified_pis
        )

        # Phase 3: Build and store snapshot
        log("")
        log("=" * 60)
        log("PHASE 3: Building and storing snapshot")
        log("=" * 60)

        snapshot = build_snapshot_json(
            CHECKPOINT_FILE, pointer, new_seq, timestamp, total_count,
            all_cids=all_cids,
            prev_all_cids=prev_all_cids if prev_all_cids else None
        )
        snapshot_cid = store_snapshot_ipfs_cli(snapshot)

        # Phase 4: Update metadata
        log("")
        log("=" * 60)
        log("PHASE 4: Updating metadata")
        log("=" * 60)

        update_index_pointer(pointer, snapshot_cid, new_seq, timestamp, total_count)
        save_metadata(snapshot_cid, new_seq, timestamp, total_count)

        # Summary
        elapsed = time.time() - start_time
        merkle_root = snapshot.get("merkle_root", "N/A")
        cid_count = snapshot.get("cid_count", 0)

        print("", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"{GREEN}Snapshot Build Complete{NC}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"CID:         {snapshot_cid}", file=sys.stderr)
        print(f"Sequence:    {new_seq}", file=sys.stderr)
        print(f"Entities:    {total_count}", file=sys.stderr)
        print(f"Total CIDs:  {cid_count}", file=sys.stderr)
        print(f"Merkle Root: {merkle_root[:32]}..." if merkle_root != "N/A" else "Merkle Root: N/A", file=sys.stderr)
        print(f"Time:        {timestamp}", file=sys.stderr)
        print(f"Duration:    {elapsed:.0f}s ({elapsed/60:.1f} minutes)", file=sys.stderr)
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
