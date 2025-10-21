# DAG-JSON vs JSON: Critical Differences for IPLD Links

## TL;DR

**Always use `--input-codec=dag-json` when storing IPFS DAG nodes with links.**

Using `--input-codec=json` creates **fake links** (plain CBOR maps) that look correct but break DAG traversal, CAR exports, and disaster recovery.

---

## The Problem We Discovered

### What Went Wrong

When we first implemented the DR scripts, we used:

```bash
ipfs dag put --store-codec=dag-cbor --input-codec=json --pin=true
```

This created manifests that **appeared** to work:
- ✅ `ipfs dag get` showed links as `{"/": "bafyrei..."}`
- ✅ Components could be fetched individually
- ✅ Version chains displayed correctly

But **silently broke** critical functionality:
- ❌ `ipfs dag export` only exported 13/20 blocks (missing all components!)
- ❌ CAR files were incomplete and unusable for DR
- ❌ Hexdump showed NO CBOR tag-42 markers (proof links were fake)

### Root Cause

The `input-codec` parameter determines how IPFS **interprets** the input JSON:

| Input Codec | How `{"/": "cid"}` is Stored | CBOR Encoding | DAG Traversal |
|-------------|------------------------------|---------------|---------------|
| `json` | Plain CBOR map with string key `"/"` | `a1 61 2f` (map) | ❌ **NOT followed** |
| `dag-json` | Typed IPLD Link | `d8 2a` (tag-42 + CID bytes) | ✅ **Followed** |

---

## Technical Deep Dive

### IPLD Data Model

IPLD (InterPlanetary Linked Data) has distinct **kinds**:

- **Map** - key-value pairs (like JSON objects)
- **Link** - typed reference to another block (special kind, not a map!)
- **String**, **Int**, **Bytes**, etc.

A **Link** is NOT a map that happens to have a `"/"` key—it's a first-class type.

### DAG-JSON Codec Spec

[DAG-JSON](https://ipld.io/docs/codecs/known/dag-json/) is a **strict subset** of JSON with special handling:

```json
{
  "prev": {"/": "bafyrei..."}  ← Interpreted as Link (if using dag-json codec)
}
```

When `input-codec=dag-json`:
1. Parser recognizes `{"/": "..."}` as IPLD link syntax
2. Validates the CID (will error on invalid CIDs!)
3. Stores as **CBOR tag-42** (typed link) when using `store-codec=dag-cbor`

When `input-codec=json`:
1. Parser treats `{"/": "..."}` as ordinary map
2. No CID validation
3. Stores as plain CBOR map (bytes: `a1 61 2f ...`)

### CBOR Encoding Proof

We can verify this with hexdump:

**Fake Link** (`input-codec=json`):
```
00000050  70 72 65 76 a1 61 2f 78  3b 62 61 66 79 72 65 69  |prev.a/x;bafyrei|
                      ^^^^^^^^^^
                      a1 = map with 1 entry
                      61 = string length 1
                      2f = "/" (ASCII 0x2F)
```

**Real Link** (`input-codec=dag-json`):
```
00000050  70 72 65 76 d8 2a 58 25  00 01 71 12 20 48 05 31  |prev.*X%..q. H.1|
                      ^^^^^^
                      d8 2a = CBOR tag 42 (IPLD link)
                      58 25 = byte string of length 37 (CID bytes)
```

Tag-42 is the **definitive marker** of a real IPLD link in CBOR.

---

## Why DAG Traversal Breaks

### How `ipfs dag export` Works

```
1. Start at root CID (snapshot)
2. Load block from blockstore
3. Decode as dag-cbor
4. Scan for Link-typed values  ← CRITICAL
5. For each Link:
   - Add linked CID to export queue
   - Recursively export that block
6. Write all blocks to CAR file
```

**Step 4 is the key**: IPFS only follows **Link kinds**, not arbitrary maps.

If `components.metadata` is stored as:
```cbor
a1 61 2f 78 3b ...  (map with key "/")
```

The traverser sees: "This is a Map kind containing a String. Skip it."

If stored as:
```cbor
d8 2a 58 25 ...  (tag-42 Link)
```

The traverser sees: "This is a Link kind. Follow it!"

---

## Command Comparison

### ❌ BROKEN: Using `input-codec=json`

```bash
# CLI
echo '{
  "schema": "arke/manifest/v1",
  "pi": "01ABC...",
  "components": {
    "metadata": {"/": "bafyrei..."}
  }
}' | ipfs dag put --store-codec=dag-cbor --input-codec=json --pin=true
```

**Result:**
- CID: `bafyreiabc123...` ✓ (works)
- Retrieval: `ipfs dag get bafyreiabc123...` ✓ (works)
- Export: `ipfs dag export bafyreiabc123...` ❌ (missing component blocks!)
- Tag-42 check: `hexdump | grep "d8 2a"` ❌ (none found at `components.metadata`)

**Why it's broken:**
- Links stored as plain maps
- NOT recognized by DAG traversal
- Components excluded from CAR exports
- **Disaster recovery fails**

---

### ✅ CORRECT: Using `input-codec=dag-json`

```bash
# CLI
echo '{
  "schema": "arke/manifest/v1",
  "pi": "01ABC...",
  "components": {
    "metadata": {"/": "bafyrei..."}
  }
}' | ipfs dag put --store-codec=dag-cbor --input-codec=dag-json --pin=true
```

**Result:**
- CID: `bafyreiabc456...` ✓ (different CID due to different encoding!)
- Retrieval: `ipfs dag get bafyreiabc456...` ✓ (works)
- Export: `ipfs dag export bafyreiabc456...` ✓ (includes ALL linked blocks!)
- Tag-42 check: `hexdump | grep "d8 2a"` ✓ (found at `prev` AND `components.metadata`)

**Why it works:**
- Links stored as CBOR tag-42
- Recognized by DAG traversal
- Components included in CAR exports
- **Disaster recovery works**

---

## HTTP API Equivalent

The Kubo HTTP RPC API at `/api/v0/dag/put` works the same way:

### Python Example

```python
import httpx

IPFS_API = "http://localhost:5001/api/v0"

manifest = {
    "schema": "arke/manifest/v1",
    "pi": "01ABC...",
    "ver": 1,
    "prev": {"/": "bafyrei..."},  # IPLD link
    "components": {
        "metadata": {"/": "bafyrei..."}  # IPLD link
    }
}

# ❌ BROKEN
response = httpx.post(
    f"{IPFS_API}/dag/put",
    params={
        "store-codec": "dag-cbor",
        "input-codec": "json",  # ← WRONG
        "pin": "true"
    },
    json=manifest
)

# ✅ CORRECT
response = httpx.post(
    f"{IPFS_API}/dag/put",
    params={
        "store-codec": "dag-cbor",
        "input-codec": "dag-json",  # ← CORRECT
        "pin": "true"
    },
    json=manifest
)
```

### TypeScript Example

```typescript
const manifest = {
  schema: "arke/manifest/v1",
  pi: "01ABC...",
  prev: { "/": "bafyrei..." },
  components: {
    metadata: { "/": "bafyrei..." }
  }
};

// ✅ CORRECT
const response = await fetch(
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=dag-json&pin=true",
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(manifest)
  }
);
```

---

## Verification Methods

### 1. Hexdump Test (Definitive)

```bash
# Get manifest as CBOR
ipfs dag get --output-codec=dag-cbor <manifest_cid> > manifest.cbor

# Count CBOR tag-42 occurrences
hexdump -C manifest.cbor | grep -c "d8 2a"

# Expected count = number of IPLD links in manifest
# e.g., 3 for (prev + components.metadata + components.image)
```

### 2. DAG Export Block Count

```bash
# Export to CAR
ipfs dag export <snapshot_cid> > export.car

# Import and count
ipfs dag import --stats export.car
# Should show: "Imported N blocks" where N includes ALL referenced blocks
```

### 3. Path Resolution Test

```bash
# Real links resolve
ipfs dag get <manifest_cid>/components/metadata
# → Fetches the metadata block (if real link)

# Fake links don't resolve
ipfs dag get <manifest_cid>/components/metadata
# → Returns the map {"/":" bafyrei..."} (if fake link)
```

---

## Impact on Our System

### Before Fix (`input-codec=json`)

- Manifests: 6 blocks ✓
- Components: 0 blocks ❌ (not traversed)
- Events: 6 blocks ✓
- **Total in CAR: 13 blocks**

CAR export command:
```bash
ipfs dag export <snapshot_cid>
```

Result: **Incomplete export, DR broken**

### After Fix (`input-codec=dag-json`)

- Manifests: 6 blocks ✓
- Components: 7 blocks ✓ (now traversed!)
- Events: 6 blocks ✓
- **Total in CAR: 20 blocks**

CAR export command:
```bash
ipfs dag export <snapshot_cid>
```

Result: **Complete export, DR works**

---

## Best Practices

### 1. Always Use `dag-json` for Structured Data with Links

```bash
# ✅ For manifests, snapshots, events (anything with IPLD links)
--input-codec=dag-json --store-codec=dag-cbor

# ❌ Never use plain json for structured DAG data
--input-codec=json --store-codec=dag-cbor
```

### 2. Use `dag-json` for Snapshots Too

Even though snapshots are stored as `dag-json` (not `dag-cbor`):

```bash
# ✅ CORRECT
ipfs dag put --store-codec=dag-json --input-codec=dag-json --pin=true

# ❌ WRONG (links won't be typed correctly)
ipfs dag put --store-codec=dag-json --input-codec=json --pin=true
```

### 3. Test Your DR Pipeline

```bash
# Full DR test
1. Create test data
2. Build snapshot
3. Export CAR
4. Count blocks: ipfs dag import --stats test.car
5. Verify block count matches expectations
```

### 4. Add Hexdump Checks in CI

```bash
# Verify manifest has correct link encoding
manifest_cid=$(...)
ipfs dag get --output-codec=dag-cbor $manifest_cid > /tmp/manifest.cbor
tag42_count=$(hexdump -C /tmp/manifest.cbor | grep -c "d8 2a")

if [ $tag42_count -lt 1 ]; then
  echo "ERROR: Manifest has no IPLD links (tag-42 not found)"
  exit 1
fi
```

---

## Common Mistakes

### ❌ "It works in dag/get so it must be fine"

```bash
ipfs dag get <cid>
# Shows: {"components": {"metadata": {"/": "bafyrei..."}}}
```

**This is misleading!** DAG-JSON output format uses `{"/":"..."}` regardless of how it's stored internally.

**Verify with:** hexdump or export test

### ❌ "I'll just fix it later"

Once data is stored with fake links, you must:
1. Read old manifest
2. Rebuild with correct link types
3. Store as new manifest (new CID)
4. Update all references

**Fix it from the start.**

### ❌ "Only manifests need dag-json"

**Wrong.** Use `dag-json` for:
- ✅ Manifests (have `prev`, `components` links)
- ✅ Events (have `prev`, `tip_cid` links)
- ✅ Snapshots (have `prev_snapshot`, `entries[].tip_cid` links)
- ✅ Any structure with IPLD links

---

## Summary Table

| Feature | `input-codec=json` | `input-codec=dag-json` |
|---------|-------------------|----------------------|
| Accepts `{"/":"cid"}` syntax | ✅ Yes | ✅ Yes |
| Validates CIDs | ❌ No | ✅ Yes (errors on invalid CIDs) |
| Stores as typed IPLD Link | ❌ No (plain map) | ✅ Yes (CBOR tag-42) |
| `ipfs dag get` works | ✅ Yes | ✅ Yes |
| `ipfs dag export` follows links | ❌ **NO** | ✅ **YES** |
| CAR files complete | ❌ **NO** | ✅ **YES** |
| DR/backup works | ❌ **NO** | ✅ **YES** |
| Use for production | ❌ **NEVER** | ✅ **ALWAYS** |

---

## References

- [IPLD DAG-JSON Spec](https://ipld.io/docs/codecs/known/dag-json/)
- [IPLD Data Model](https://ipld.io/docs/data-model/)
- [CBOR Tag Registry](https://www.iana.org/assignments/cbor-tags/cbor-tags.xhtml) - Tag 42 is "IPLD content identifier"
- [Kubo dag/put RPC docs](https://docs.ipfs.tech/reference/kubo/rpc/#api-v0-dag-put)

---

**Last Updated:** 2025-10-21
**Tested With:** Kubo v0.31.0

**Critical Takeaway:** `input-codec=dag-json` is not optional—it's **required** for any IPFS data structure with IPLD links. Without it, disaster recovery silently breaks.
