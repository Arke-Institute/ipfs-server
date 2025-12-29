# Append-Only Proofs

Cryptographic proof that the Arke archive is append-only: data is only ever added, never deleted.

## Overview

Starting with snapshot schema v2, every snapshot includes a Merkle tree root computed from ALL CIDs in the archive. This enables:

1. **Deletion detection** - If any historical version is removed, the Merkle root changes
2. **Verifiable completeness** - Anyone can verify a CAR export contains all expected data
3. **Third-party auditability** - No need to trust Arke; just verify the math

## How It Works

### The Problem

Without cryptographic proofs, an operator could:
- Delete old entity versions to save space
- Remove controversial content retroactively
- Claim data never existed

The event log would still show operations happened, but the actual blocks would be gone.

### The Solution

Each snapshot now includes:

```json
{
  "schema": "arke/snapshot@v2",
  "merkle_root": "18b584f72e70e9396b3c28b760ad5dd8...",
  "cid_count": 28997,
  "all_cids": ["bafkrei...", "bafkrei...", ...],
  "consistency": {
    "prev_cid_count": 28990,
    "curr_cid_count": 28997,
    "added_count": 7,
    "deleted_count": 0,
    "is_append_only": true
  },
  "entries": [...]
}
```

**Key fields:**
- `merkle_root` - SHA256 root of Merkle tree built from sorted CID list
- `all_cids` - Complete list of every CID in the archive (manifests + components)
- `consistency` - Comparison with previous snapshot proving append-only property

### What Gets Included in the Proof

For each entity, we collect:
1. **All manifest CIDs** - Current version AND all previous versions (via `prev` links)
2. **All component CIDs** - Metadata, images, files from each manifest
3. **Chain entry CIDs** - Event log entries

This means deleting ANY historical version changes the Merkle root.

### What's NOT Included

**Test network entities are excluded from proofs.** The Arke system maintains separate storage for test and production data:

| Network | MFS Path | Included in Proofs |
|---------|----------|-------------------|
| Main | `/arke/index/{shard}/{pi}.tip` | ✅ Yes |
| Test | `/arke/test/index/{shard}/{pi}.tip` | ❌ No |

Test entities are identified by their `II` prefix (e.g., `IIAK75HQQXNTDG7BBP7PS9AWY`). They:
- Use a separate MFS directory (`/arke/test/`)
- Have their own event chain (or none)
- Are designed for integration testing, not permanent archival

This is intentional - test data is ephemeral and shouldn't be mixed with production proofs. If test network proofs were ever needed, a separate snapshot system would be required.

---

## Verification

### Comparing Two Snapshots

```python
from dr.build_snapshot import build_merkle_root, generate_consistency_info

# Load snapshots
snapshot_old = ipfs.dag.get(old_snapshot_cid)
snapshot_new = ipfs.dag.get(new_snapshot_cid)

# Get CID sets
old_cids = set(snapshot_old["all_cids"])
new_cids = set(snapshot_new["all_cids"])

# Verify append-only
consistency = generate_consistency_info(old_cids, new_cids)

if consistency["is_append_only"]:
    print(f"✓ Verified: {consistency['added_count']} CIDs added, none deleted")
else:
    print(f"✗ VIOLATION: {consistency['deleted_count']} CIDs were deleted!")
```

### Verifying CAR Export Completeness

```python
import car  # hypothetical CAR parsing library

# Parse CAR file
car_cids = set(car.extract_block_cids("arke-snapshot.car"))

# Compare to snapshot's claimed CIDs
snapshot = ipfs.dag.get(snapshot_cid)
expected_cids = set(snapshot["all_cids"])

missing = expected_cids - car_cids
if missing:
    print(f"CAR is incomplete! Missing {len(missing)} blocks")
else:
    print("CAR contains all expected blocks")
```

### Verifying Merkle Root

```python
from dr.build_snapshot import build_merkle_root

# Recompute root from CID list
computed_root, _ = build_merkle_root(set(snapshot["all_cids"]))

# Compare to stored root
if computed_root == snapshot["merkle_root"]:
    print("✓ Merkle root verified")
else:
    print("✗ Merkle root mismatch - data has been tampered!")
```

---

## Implementation Details

### Merkle Tree Construction

Uses a simple binary Merkle tree with SHA256:

```
Leaves (sorted CIDs):  CID₁   CID₂   CID₃   CID₄
                         \    /       \    /
Level 1:                 H₁₂          H₃₄
                            \        /
Root:                        ROOT
```

- Leaves are sorted alphabetically for deterministic ordering
- Each leaf is hashed: `SHA256(cid_string)`
- Parent nodes: `SHA256(left_child || right_child)`
- Odd nodes are duplicated (hashed with themselves)

### Incremental Collection

For efficiency, subsequent snapshots don't re-walk all version chains:

1. Load `all_cids` from previous v2 snapshot
2. Identify modified entities from event chain
3. Only walk version chains for modified entities
4. Merge new CIDs with previous set

This reduces collection time from O(all entities) to O(modified entities).

### Performance

Tested on production data (3,285 entities):

| Operation | Time | Notes |
|-----------|------|-------|
| Full CID collection | ~70s | First v2 snapshot or after v1 |
| Incremental collection | ~5-15s | Typical, depends on changes |
| Merkle tree build | 0.09s | 29K CIDs, essentially instant |

---

## Snapshot Schema v2

### Full Schema

```json
{
  "schema": "arke/snapshot@v2",
  "seq": 157,
  "ts": "2025-12-28T12:00:00Z",
  "event_cid": "bafyrei...",
  "total_count": 3285,
  "prev_snapshot": {"/": "baguqee..."},

  "merkle_root": "18b584f72e70e9396b3c28b760ad5dd8ab6e54bf2aa772a21816c74fb5527cce",
  "cid_count": 28997,
  "all_cids": [
    "bafkreia...",
    "bafkreib...",
    "..."
  ],
  "consistency": {
    "prev_cid_count": 28990,
    "curr_cid_count": 28997,
    "added_count": 7,
    "deleted_count": 0,
    "is_append_only": true
  },

  "entries": [
    {"pi": "01K75...", "ver": 3, "tip_cid": {"/": "..."}, "chain_cid": {"/": "..."}}
  ]
}
```

### Backwards Compatibility

- v1 snapshots work normally (no proof fields)
- First v2 build after v1 does full CID collection
- Subsequent v2 builds use incremental collection

---

## Integration with CAR Exports

The proof is automatically included in CAR exports because:

1. Snapshot object contains `all_cids` array
2. Each CID in array is stored as plain string (not IPLD link)
3. CAR exporter includes the snapshot block
4. Verifier can extract `all_cids` and compare to actual blocks in CAR

### Workflow

```
build_snapshot.py
  ├─ Collect entries
  ├─ Collect ALL CIDs (walk version chains)
  ├─ Build Merkle tree → merkle_root
  ├─ Store snapshot (includes proof)
  │
  ▼
daily-car-export.sh
  ├─ Export snapshot DAG to CAR
  │   └─ CAR includes: snapshot + all linked blocks
  │
  ▼
Verification
  ├─ Parse CAR, extract block CIDs
  ├─ Compare to snapshot.all_cids
  └─ Recompute and verify merkle_root
```

---

## Future Enhancements

### 1. On-Chain Anchoring

Publish `merkle_root` to a blockchain for tamper-proof timestamping:

```solidity
contract ArkeProofs {
    mapping(uint256 => bytes32) public roots;  // seq → merkle_root

    function commitRoot(uint256 seq, bytes32 root) external onlyOperator {
        roots[seq] = root;
        emit RootCommitted(seq, root);
    }
}
```

Cost: ~$0.001/day on Solana, ~$0.01/day on Polygon

### 2. Inclusion Proofs

Prove a specific entity exists in the archive:

```python
def prove_inclusion(entity_cid, all_cids, merkle_tree):
    """Generate O(log n) proof that entity_cid is in tree."""
    leaf_index = all_cids.index(entity_cid)
    return merkle_tree.get_proof(leaf_index)
```

### 3. RFC 6962 Compliance

Full Certificate Transparency-style proofs:
- Use `pymerkle` library for proper consistency proofs
- O(log n) proof size instead of full CID comparison
- Compatible with existing CT verification tools

### 4. Witness Network

Third parties independently verify and attest:
- Multiple witnesses store merkle roots
- Dead man's switch if roots stop appearing
- Community alerts on consistency failures

---

## Troubleshooting

### Snapshot Build Slow

CID collection walks all version chains. If slow:

1. Check network latency to IPFS node
2. Verify incremental mode is working (should only walk modified entities)
3. Consider parallelizing collection (future optimization)

### Consistency Check Fails

If `is_append_only: false`:

1. **Legitimate**: Garbage collection ran and removed old versions
2. **Bug**: CID collection missed some blocks
3. **Tampering**: Someone deleted data

Investigate by comparing `all_cids` between snapshots:
```python
deleted = set(old_snapshot["all_cids"]) - set(new_snapshot["all_cids"])
for cid in deleted:
    print(f"Missing: {cid}")
```

### Merkle Root Mismatch

If recomputed root doesn't match stored root:

1. Verify `all_cids` array wasn't modified
2. Check for encoding issues (CIDs must be exact strings)
3. Ensure deterministic sorting (alphabetical)

---

## References

- [RFC 6962: Certificate Transparency](https://www.rfc-editor.org/rfc/rfc6962.html)
- [Transparent Logs for Skeptical Clients](https://research.swtch.com/tlog) - Excellent explainer
- [pymerkle](https://pymerkle.readthedocs.io/) - Python RFC 6962 implementation
- [merkletreejs](https://github.com/merkletreejs/merkletreejs) - JavaScript implementation

---

## Files

| File | Purpose |
|------|---------|
| `api/dr/build_snapshot.py` | Main implementation |
| `scripts/test_append_only_proof.py` | Standalone test script |
| `scripts/test_proof_integration.py` | Integration test |

---

**Last Updated**: 2025-12-27
**Status**: Implemented and tested
