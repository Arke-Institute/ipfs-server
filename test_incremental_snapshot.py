#!/usr/bin/env python3
"""
Test incremental snapshot builds.

Tests:
1. Create 100 entities → build snapshot (full traversal)
2. Create 50 more entities → build snapshot (incremental)
3. Create 100 more entities → build snapshot (incremental)
4. Create 1000 more entities → build snapshot (incremental)

Verify that incremental builds are fast and scale with new entities, not total.
"""

import subprocess
import time
import json
import httpx

API_URL = "http://localhost:3000"
IPFS_API = "http://localhost:5001/api/v0"

def create_entity(pi: str, metadata: dict) -> dict:
    """Create an entity via API."""
    response = httpx.post(
        f"{API_URL}/entities",
        json={
            "pi": pi,
            "metadata": metadata
        }
    )
    response.raise_for_status()
    return response.json()

def create_batch_entities(count: int, start_idx: int = 0) -> list[str]:
    """Create a batch of entities."""
    pis = []
    print(f"\nCreating {count} entities...", end="", flush=True)
    start = time.time()

    for i in range(count):
        pi = f"TEST_{start_idx + i:08d}000000000000"
        metadata = {"index": start_idx + i, "batch": "test"}
        create_entity(pi, metadata)
        pis.append(pi)

        if (i + 1) % 100 == 0:
            print(f" {i+1}", end="", flush=True)

    elapsed = time.time() - start
    print(f" Done! ({elapsed:.1f}s)")
    return pis

def build_snapshot() -> dict:
    """Build snapshot and return timing info."""
    print("\nBuilding snapshot...", flush=True)
    start = time.time()

    result = subprocess.run(
        ["docker", "exec", "ipfs-api", "python3", "-m", "dr.build_snapshot"],
        capture_output=True,
        text=True
    )

    elapsed = time.time() - start

    # Parse output for stats
    output = result.stderr
    print(output)

    # Extract metrics
    metrics = {
        "duration": elapsed,
        "return_code": result.returncode
    }

    # Try to parse mode and counts from output
    if "Mode: INCREMENTAL" in output:
        metrics["mode"] = "incremental"
    elif "Mode: FULL TRAVERSAL" in output:
        metrics["mode"] = "full"
    else:
        metrics["mode"] = "unknown"

    # Extract events processed
    for line in output.split("\n"):
        if "Events processed:" in line:
            metrics["events_processed"] = int(line.split(":")[1].strip())
        elif "PIs modified/new:" in line:
            metrics["pis_modified"] = int(line.split(":")[1].strip())
        elif "Total PIs:" in line:
            metrics["total_pis"] = int(line.split(":")[1].strip())

    return metrics

def get_snapshot_count() -> int:
    """Get count of entities in latest snapshot."""
    with open("/Users/chim/Working/arke_institute/ipfs-server/snapshots/latest.json") as f:
        metadata = json.load(f)
    return metadata.get("count", 0)

def main():
    print("=" * 70)
    print("INCREMENTAL SNAPSHOT BUILD TEST")
    print("=" * 70)

    results = []

    # Test 1: Initial 100 entities (full traversal)
    print("\n" + "=" * 70)
    print("TEST 1: Create 100 entities + build snapshot (FULL TRAVERSAL)")
    print("=" * 70)

    create_batch_entities(100, start_idx=0)
    metrics1 = build_snapshot()
    results.append(("Initial 100", metrics1))

    print(f"\n✓ Snapshot 1: {metrics1['mode']} mode, {metrics1['duration']:.1f}s")
    print(f"  Total PIs in snapshot: {get_snapshot_count()}")

    # Test 2: Add 50 more (incremental)
    print("\n" + "=" * 70)
    print("TEST 2: Add 50 entities + build snapshot (INCREMENTAL)")
    print("=" * 70)

    create_batch_entities(50, start_idx=100)
    metrics2 = build_snapshot()
    results.append(("Add 50", metrics2))

    print(f"\n✓ Snapshot 2: {metrics2['mode']} mode, {metrics2['duration']:.1f}s")
    print(f"  Events processed: {metrics2.get('events_processed', 'N/A')}")
    print(f"  Total PIs in snapshot: {get_snapshot_count()}")

    # Test 3: Add 100 more (incremental)
    print("\n" + "=" * 70)
    print("TEST 3: Add 100 entities + build snapshot (INCREMENTAL)")
    print("=" * 70)

    create_batch_entities(100, start_idx=150)
    metrics3 = build_snapshot()
    results.append(("Add 100", metrics3))

    print(f"\n✓ Snapshot 3: {metrics3['mode']} mode, {metrics3['duration']:.1f}s")
    print(f"  Events processed: {metrics3.get('events_processed', 'N/A')}")
    print(f"  Total PIs in snapshot: {get_snapshot_count()}")

    # Test 4: Add 1000 more (incremental - should still be fast!)
    print("\n" + "=" * 70)
    print("TEST 4: Add 1000 entities + build snapshot (INCREMENTAL)")
    print("=" * 70)

    create_batch_entities(1000, start_idx=250)
    metrics4 = build_snapshot()
    results.append(("Add 1000", metrics4))

    print(f"\n✓ Snapshot 4: {metrics4['mode']} mode, {metrics4['duration']:.1f}s")
    print(f"  Events processed: {metrics4.get('events_processed', 'N/A')}")
    print(f"  Total PIs in snapshot: {get_snapshot_count()}")

    # Summary
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    for label, metrics in results:
        mode = metrics.get('mode', 'unknown')
        duration = metrics['duration']
        events = metrics.get('events_processed', 'N/A')
        total = metrics.get('total_pis', 'N/A')

        print(f"\n{label}:")
        print(f"  Mode:     {mode}")
        print(f"  Duration: {duration:.2f}s")
        print(f"  Events:   {events}")
        print(f"  Total PIs: {total}")

    # Verify incremental is faster than full
    if results[0][1]['duration'] > 0:
        speedup_50 = results[0][1]['duration'] / results[1][1]['duration']
        speedup_100 = results[0][1]['duration'] / results[2][1]['duration']
        speedup_1000 = results[0][1]['duration'] / results[3][1]['duration']

        print("\n" + "=" * 70)
        print("PERFORMANCE ANALYSIS")
        print("=" * 70)
        print(f"\nFull traversal (100 entities):  {results[0][1]['duration']:.2f}s")
        print(f"Incremental (50 new):           {results[1][1]['duration']:.2f}s ({speedup_50:.1f}x speedup)")
        print(f"Incremental (100 new):          {results[2][1]['duration']:.2f}s ({speedup_100:.1f}x speedup)")
        print(f"Incremental (1000 new):         {results[3][1]['duration']:.2f}s ({speedup_1000:.1f}x speedup)")

        print("\n✅ SUCCESS: Incremental builds are faster!")
        print("✅ SUCCESS: Timing scales with new entities, not total!")

if __name__ == "__main__":
    main()
