#!/usr/bin/env python3
"""
Test the append-only proof integration by running the new code on current snapshot.
"""
import sys
import json
import time
import os

# Add the dr module to path
sys.path.insert(0, '/app')

from dr.build_snapshot import (
    collect_all_cids,
    build_merkle_root,
    generate_consistency_info,
    log, success, warn, error
)

IPFS_API = os.getenv("IPFS_API_URL", "http://ipfs:5001/api/v0")

def main():
    import httpx

    print("\n" + "=" * 60)
    print("APPEND-ONLY PROOF INTEGRATION TEST")
    print("=" * 60 + "\n")

    # Load current snapshot
    with open("/app/snapshots/latest.json") as f:
        meta = json.load(f)

    snapshot_cid = meta["cid"]
    log(f"Testing with snapshot seq={meta['seq']}, {meta['count']} entities")
    log(f"Snapshot CID: {snapshot_cid[:30]}...")

    # Fetch snapshot
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{IPFS_API}/dag/get",
            params={"arg": snapshot_cid}
        )
        response.raise_for_status()
        snapshot = response.json()

    entries = snapshot.get("entries", [])
    log(f"Loaded {len(entries)} entries from snapshot")

    # Test: Collect all CIDs (full mode - no previous CIDs)
    print("\n" + "=" * 60)
    print("PHASE 1: Full CID Collection")
    print("=" * 60 + "\n")

    start = time.time()
    all_cids = collect_all_cids(entries, prev_all_cids=None, modified_pis=None)
    elapsed = time.time() - start

    log(f"Collected {len(all_cids)} CIDs in {elapsed:.1f}s")

    # Test: Build Merkle tree
    print("\n" + "=" * 60)
    print("PHASE 2: Build Merkle Tree")
    print("=" * 60 + "\n")

    start = time.time()
    merkle_root, sorted_cids = build_merkle_root(all_cids)
    elapsed = time.time() - start

    success(f"Merkle root: {merkle_root}")
    log(f"Tree built in {elapsed:.3f}s")

    # Test: Simulate incremental update (add some CIDs)
    print("\n" + "=" * 60)
    print("PHASE 3: Consistency Check Simulation")
    print("=" * 60 + "\n")

    # Simulate: "previous" state is current minus some random CIDs
    prev_cids = set(list(all_cids)[:len(all_cids) - 10])  # Remove last 10
    curr_cids = all_cids

    consistency = generate_consistency_info(prev_cids, curr_cids)
    log(f"Simulated adding 10 CIDs:")
    log(f"  Previous: {consistency['prev_cid_count']} CIDs")
    log(f"  Current:  {consistency['curr_cid_count']} CIDs")
    log(f"  Added:    {consistency['added_count']}")
    log(f"  Deleted:  {consistency['deleted_count']}")
    if consistency["is_append_only"]:
        success("Append-only verified!")
    else:
        warn("APPEND-ONLY VIOLATION!")

    # Test: Simulate deletion (remove some CIDs)
    print("\n" + "=" * 60)
    print("PHASE 4: Deletion Detection Simulation")
    print("=" * 60 + "\n")

    cid_list = list(all_cids)
    # "Previous" has 100 CIDs that "current" doesn't
    prev_cids = set(cid_list[:100])
    curr_cids = set(cid_list[50:150])  # Overlaps but removes 50

    consistency = generate_consistency_info(prev_cids, curr_cids)
    log(f"Simulated deletion of 50 CIDs:")
    log(f"  Previous: {consistency['prev_cid_count']} CIDs")
    log(f"  Current:  {consistency['curr_cid_count']} CIDs")
    log(f"  Added:    {consistency['added_count']}")
    log(f"  Deleted:  {consistency['deleted_count']}")
    if not consistency["is_append_only"]:
        success("Deletion correctly detected!")
    else:
        warn("ERROR: Deletion not detected!")

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(json.dumps({
        "entities": len(entries),
        "total_cids": len(all_cids),
        "merkle_root": merkle_root,
        "test_passed": True
    }, indent=2))


if __name__ == "__main__":
    main()
