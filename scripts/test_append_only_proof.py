#!/usr/bin/env python3
"""
Test script for Append-Only Proofs.
Demonstrates:
1. Collecting all CIDs from current state
2. Building a Merkle tree
3. Timing the operation
4. Simulating deletion detection
"""

import sys
import json
import time
import hashlib
import os
from typing import Set, List, Dict, Any, Tuple
import httpx

# Configuration
IPFS_API = os.getenv("IPFS_API_URL", "http://ipfs:5001/api/v0")
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


class SimpleMerkleTree:
    """
    Simple Merkle tree implementation for testing.
    In production, use pymerkle for RFC 6962 compliance.
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


def collect_version_chain(tip_cid: str, client: httpx.Client) -> List[str]:
    """Walk prev chain to collect ALL version CIDs for an entity."""
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


def collect_all_cids_from_snapshot(snapshot_cid: str, sample_size: int = None) -> Tuple[Set[str], Dict[str, Any]]:
    """
    Collect ALL CIDs referenced by a snapshot.
    If sample_size is set, only process that many entities (for testing).
    Returns (set of CIDs, snapshot metadata).
    """
    all_cids = set()
    start_time = time.time()

    with httpx.Client(timeout=TIMEOUT) as client:
        # Fetch snapshot
        log(f"Fetching snapshot: {snapshot_cid[:20]}...")
        response = client.post(
            f"{IPFS_API}/dag/get",
            params={"arg": snapshot_cid}
        )
        response.raise_for_status()
        snapshot = response.json()

        entries = snapshot.get("entries", [])
        log(f"Snapshot has {len(entries)} entities")

        if sample_size:
            entries = entries[:sample_size]
            log(f"Sampling first {sample_size} entities for testing")

        # Add snapshot CID itself
        all_cids.add(snapshot_cid)

        # Process each entity
        entities_processed = 0
        total_versions = 0

        for entry in entries:
            tip_cid = entry.get("tip_cid", {})
            if isinstance(tip_cid, dict):
                tip_cid = tip_cid.get("/")

            if not tip_cid:
                continue

            # Collect all versions and components
            version_cids = collect_version_chain(tip_cid, client)
            all_cids.update(version_cids)
            total_versions += len(version_cids)

            # Also add chain entry CID
            chain_cid = entry.get("chain_cid", {})
            if isinstance(chain_cid, dict):
                chain_cid = chain_cid.get("/")
            if chain_cid:
                all_cids.add(chain_cid)

            entities_processed += 1
            if entities_processed % 100 == 0:
                elapsed = time.time() - start_time
                log(f"Processed {entities_processed}/{len(entries)} entities "
                    f"({len(all_cids)} CIDs, {elapsed:.1f}s)")

    elapsed = time.time() - start_time
    success(f"Collected {len(all_cids)} unique CIDs from {entities_processed} entities in {elapsed:.1f}s")

    return all_cids, snapshot


def build_merkle_tree(cids: Set[str]) -> SimpleMerkleTree:
    """Build Merkle tree from CID set."""
    sorted_cids = sorted(cids)
    leaves = [cid.encode() for cid in sorted_cids]
    return SimpleMerkleTree(leaves)


def simulate_deletion_detection(all_cids: Set[str], tree: SimpleMerkleTree):
    """
    Demonstrate how deletion is detected.
    """
    print("\n" + "=" * 60)
    print(f"{YELLOW}DELETION DETECTION SIMULATION{NC}")
    print("=" * 60 + "\n")

    # Original state
    original_root = tree.root
    original_count = len(all_cids)
    print(f"Original tree:")
    print(f"  CID count:    {original_count}")
    print(f"  Merkle root:  {original_root[:32]}...")
    print()

    # Simulate deletion (remove one CID)
    cid_list = sorted(all_cids)
    deleted_cid = cid_list[len(cid_list) // 2]  # Pick middle CID

    tampered_cids = all_cids - {deleted_cid}
    tampered_tree = build_merkle_tree(tampered_cids)

    print(f"After 'deleting' 1 CID ({deleted_cid[:20]}...):")
    print(f"  CID count:    {len(tampered_cids)}")
    print(f"  Merkle root:  {tampered_tree.root[:32]}...")
    print()

    # Detection
    if original_root == tampered_tree.root:
        print(f"{RED}ERROR: Deletion NOT detected!{NC}")
    else:
        print(f"{GREEN}✓ DELETION DETECTED!{NC}")
        print(f"  Root mismatch proves tampering")
        print(f"  Original: {original_root[:32]}...")
        print(f"  Tampered: {tampered_tree.root[:32]}...")

    print()


def main():
    print("\n" + "=" * 60)
    print(f"{BLUE}APPEND-ONLY PROOF TEST{NC}")
    print("=" * 60 + "\n")

    # Get snapshot CID from command line or snapshot file
    snapshot_cid = None
    sample_size = None

    # Parse arguments
    for arg in sys.argv[1:]:
        if arg.startswith("--sample="):
            sample_size = int(arg.split("=")[1])
        elif not arg.startswith("-"):
            snapshot_cid = arg

    if not snapshot_cid:
        # Try reading from snapshots file
        snapshot_file = "/app/snapshots/latest.json"
        try:
            with open(snapshot_file) as f:
                snapshot_meta = json.load(f)
                snapshot_cid = snapshot_meta.get("cid")
                log(f"Loaded snapshot from {snapshot_file}")
                log(f"Snapshot seq: {snapshot_meta.get('seq')}")
                log(f"Entity count: {snapshot_meta.get('count')}")
        except FileNotFoundError:
            error(f"No snapshot CID provided and {snapshot_file} not found")
            error("Usage: python3 test_append_only_proof.py [snapshot_cid] [--sample=N]")
            sys.exit(1)

    if not snapshot_cid:
        error("Could not determine snapshot CID")
        sys.exit(1)

    log(f"Snapshot CID: {snapshot_cid[:30]}...")
    print()

    # Phase 1: Collect all CIDs
    print("=" * 60)
    print(f"{BLUE}PHASE 1: Collecting all CIDs{NC}")
    print("=" * 60 + "\n")

    phase1_start = time.time()
    all_cids, snapshot = collect_all_cids_from_snapshot(snapshot_cid, sample_size)
    phase1_time = time.time() - phase1_start

    print(f"\nPhase 1 complete: {len(all_cids)} CIDs in {phase1_time:.1f}s")
    print()

    # Phase 2: Build Merkle tree
    print("=" * 60)
    print(f"{BLUE}PHASE 2: Building Merkle tree{NC}")
    print("=" * 60 + "\n")

    phase2_start = time.time()
    tree = build_merkle_tree(all_cids)
    phase2_time = time.time() - phase2_start

    print(f"Tree statistics:")
    print(f"  Leaves:       {tree.leaf_count}")
    print(f"  Levels:       {len(tree.levels)}")
    print(f"  Merkle root:  {tree.root[:32]}...")
    print(f"  Build time:   {phase2_time:.3f}s")
    print()

    # Phase 3: Demonstrate deletion detection
    simulate_deletion_detection(all_cids, tree)

    # Summary
    entity_count = len(snapshot.get('entries', []))
    if sample_size:
        entity_count = min(sample_size, entity_count)

    print("=" * 60)
    print(f"{GREEN}TEST SUMMARY{NC}")
    print("=" * 60)
    print(f"  Entities:        {entity_count}")
    print(f"  Total CIDs:      {len(all_cids)}")
    print(f"  Collection time: {phase1_time:.1f}s")
    print(f"  Tree build time: {phase2_time:.3f}s")
    print(f"  Total time:      {phase1_time + phase2_time:.1f}s")
    print(f"  Merkle root:     {tree.root}")
    print("=" * 60 + "\n")

    # Output JSON for scripting
    result = {
        "snapshot_cid": snapshot_cid,
        "entity_count": entity_count,
        "cid_count": len(all_cids),
        "merkle_root": tree.root,
        "collection_time_seconds": round(phase1_time, 2),
        "tree_build_time_seconds": round(phase2_time, 3),
        "total_time_seconds": round(phase1_time + phase2_time, 2)
    }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
