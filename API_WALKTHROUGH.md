# Kubo HTTP RPC API Walkthrough

A practical guide for implementing the Arke API Service endpoints using Kubo's HTTP RPC API. This shows the exact HTTP calls needed for each API Service operation.

## Table of Contents

1. [Setup & Basics](#setup--basics)
2. [Uploading Raw Bytes (POST /upload)](#1-uploading-raw-bytes-post-upload)
3. [Creating Entities (POST /entities)](#2-creating-entities-post-entities)
4. [Adding Versions (POST /entities/{pi}/versions)](#3-adding-versions-post-entitiespi-versions)
5. [Fetching Entities (GET /entities/{pi})](#4-fetching-entities-get-entitiespi)
6. [Listing Versions (GET /entities/{pi}/versions)](#5-listing-versions-get-entitiespi-versions)
7. [Fetching Specific Versions](#6-fetching-specific-versions)
8. [Resolving PI to Tip (GET /resolve/{pi})](#7-resolving-pi-to-tip-get-resolvepi)
9. [Complete Example Flow](#complete-example-flow)
10. [Error Handling](#error-handling)

---

## Setup & Basics

### Base URL
```
http://localhost:5001/api/v0
```

### HTTP Method
All Kubo RPC calls use **POST** (even for reads).

### Common Parameters
- `arg` - Main argument (CID, path, etc.)
- Query parameters are URL-encoded

---

## 1. Uploading Raw Bytes (POST /upload)

**API Service Endpoint:** `POST /upload`
**Purpose:** Upload raw files and get CIDs to reference in manifests.

### Kubo RPC Call

```bash
curl -X POST \
  -F "file=@metadata.json" \
  -F "file=@image.png" \
  "http://localhost:5001/api/v0/add?quieter=true&cid-version=1&pin=false"
```

**Query Parameters:**
- `quieter=true` - Only return final result (not progress)
- `cid-version=1` - Use CIDv1 (recommended, base32 encoded)
- `pin=false` - Don't pin yet (we'll pin manifests instead)

**Response:**
```json
{"Name":"metadata.json","Hash":"bafybeiabc123...","Size":"1234"}
{"Name":"image.png","Hash":"bafybeixyz789...","Size":"5678"}
```

**Implementation Notes:**
- Upload each component file separately or together
- Save the returned CIDs - you'll use them in manifest creation
- Files are temporarily stored; pin manifests to retain them

---

## 2. Creating Entities (POST /entities)

**API Service Endpoint:** `POST /entities`
**Purpose:** Create a new entity (PI) with manifest v1.

### Request Body
```json
{
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",  // optional, generate ULID if not provided
  "components": {
    "metadata": "bafybeiabc123...",
    "image": "bafybeixyz789..."
  },
  "children_pi": ["01GX...", "01GZ..."],
  "note": "Initial version"
}
```

### Step 1: Build Manifest Object

```javascript
const manifest = {
  schema: "arke/manifest/v1",
  pi: pi || ulid(),  // Generate if not provided
  ver: 1,
  ts: new Date().toISOString(),
  prev: null,  // No previous version
  components: {
    metadata: { "/": "bafybeiabc123..." },
    image: { "/": "bafybeixyz789..." }
  },
  children_pi: ["01GX...", "01GZ..."],
  note: "Initial version"
};
```

### Step 2: Store Manifest with DAG Put

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "schema": "arke/manifest/v1",
    "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
    "ver": 1,
    "ts": "2024-01-15T10:30:00Z",
    "prev": null,
    "components": {
      "metadata": {"/": "bafybeiabc123..."},
      "image": {"/": "bafybeixyz789..."}
    },
    "children_pi": ["01GX...", "01GZ..."],
    "note": "Initial version"
  }' \
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true"
```

**Query Parameters:**
- `store-codec=dag-cbor` - Store as CBOR (efficient binary)
- `input-codec=json` - Input is JSON
- `pin=true` - Pin the manifest (important!)

**Response:**
```json
{"Cid":{"/":"bafyreimani123..."}}
```

### Step 3: Create MFS Directory Structure

Calculate directory path from PI:
```javascript
// PI: 01HQZXY9M6K8N2P4R6T8V0W2
// First 2 chars: 01
// Next 2 chars: HQ
// Path: /arke/index/01/HQ/
const pi = "01HQZXY9M6K8N2P4R6T8V0W2";
const dir = `/arke/index/${pi.slice(0,2)}/${pi.slice(2,4)}`;
```

Create directory:
```bash
curl -X POST \
  "http://localhost:5001/api/v0/files/mkdir?arg=/arke/index/01/HQ&parents=true"
```

### Step 4: Write .tip File

```bash
echo "bafyreimani123..." | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip&create=true&truncate=true"
```

**Query Parameters:**
- `arg` - MFS path for .tip file
- `create=true` - Create file if doesn't exist
- `truncate=true` - Overwrite if exists

### Step 5: Verify (Optional)

```bash
# Read the .tip file
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip"

# Output: bafyreimani123...
```

### API Service Response

```json
{
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "ver": 1,
  "manifest_cid": "bafyreimani123...",
  "tip": "bafyreimani123..."
}
```

---

## 3. Adding Versions (POST /entities/{pi}/versions)

**API Service Endpoint:** `POST /entities/{pi}/versions`
**Purpose:** Add a new version with CAS (Compare-And-Swap) to prevent conflicts.

### Request Body
```json
{
  "expect_tip": "bafyreimani123...",  // Required for CAS
  "components": {
    "metadata": "bafybeinew456..."  // Partial update
  },
  "children_pi_add": ["01ABC..."],
  "children_pi_remove": ["01GZ..."],
  "note": "Updated metadata"
}
```

### Step 1: Read Current Tip

```bash
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip"

# Response: bafyreimani123...
```

**CAS Check:**
```javascript
const currentTip = response.trim();
if (currentTip !== expect_tip) {
  return { error: 409, message: "Conflict: tip has changed" };
}
```

### Step 2: Fetch Old Manifest

```bash
curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=bafyreimani123..."
```

**Response:**
```json
{
  "schema": "arke/manifest/v1",
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "ver": 1,
  "ts": "2024-01-15T10:30:00Z",
  "prev": null,
  "components": {
    "metadata": {"/": "bafybeiabc123..."},
    "image": {"/": "bafybeixyz789..."}
  },
  "children_pi": ["01GX...", "01GZ..."],
  "note": "Initial version"
}
```

### Step 3: Build New Manifest

```javascript
const newManifest = {
  schema: oldManifest.schema,
  pi: oldManifest.pi,
  ver: oldManifest.ver + 1,  // Increment version
  ts: new Date().toISOString(),
  prev: { "/": "bafyreimani123..." },  // Link to previous

  // Merge components (partial update)
  components: {
    ...oldManifest.components,
    metadata: { "/": "bafybeinew456..." }  // Update metadata, keep image
  },

  // Update children
  children_pi: [
    ...oldManifest.children_pi.filter(pi => pi !== "01GZ..."),  // Remove
    "01ABC..."  // Add
  ],

  note: "Updated metadata"
};
```

### Step 4: Store New Manifest

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "schema": "arke/manifest/v1",
    "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
    "ver": 2,
    "ts": "2024-01-15T11:00:00Z",
    "prev": {"/": "bafyreimani123..."},
    "components": {
      "metadata": {"/": "bafybeinew456..."},
      "image": {"/": "bafybeixyz789..."}
    },
    "children_pi": ["01GX...", "01ABC..."],
    "note": "Updated metadata"
  }' \
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true"
```

**Response:**
```json
{"Cid":{"/":"bafyreinew789..."}}
```

### Step 5: Update .tip File

```bash
echo "bafyreinew789..." | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip&create=true&truncate=true"
```

### Step 6: Swap Pins (Efficient)

```bash
curl -X POST \
  "http://localhost:5001/api/v0/pin/update?arg=bafyreimani123...&arg=bafyreinew789..."
```

**Benefits:**
- More efficient than `pin/rm` + `pin/add`
- Atomic operation
- Shared blocks aren't re-pinned

### API Service Response

```json
{
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "ver": 2,
  "manifest_cid": "bafyreinew789...",
  "tip": "bafyreinew789..."
}
```

---

## 4. Fetching Entities (GET /entities/{pi})

**API Service Endpoint:** `GET /entities/{pi}?resolve=cids`
**Purpose:** Fetch latest manifest, optionally expand components.

### Step 1: Read Tip

```bash
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip"

# Response: bafyreinew789...
```

### Step 2: Fetch Manifest

```bash
curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=bafyreinew789..."
```

**Response (resolve=cids):**
```json
{
  "schema": "arke/manifest/v1",
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "ver": 2,
  "ts": "2024-01-15T11:00:00Z",
  "prev": {"/": "bafyreimani123..."},
  "components": {
    "metadata": {"/": "bafybeinew456..."},
    "image": {"/": "bafybeixyz789..."}
  },
  "children_pi": ["01GX...", "01ABC..."],
  "note": "Updated metadata"
}
```

### Step 3: Resolve Components (resolve=bytes)

If `resolve=bytes`, fetch each component:

```bash
# Fetch metadata
curl -X POST \
  "http://localhost:5001/api/v0/cat?arg=bafybeinew456..."

# Fetch image
curl -X POST \
  "http://localhost:5001/api/v0/cat?arg=bafybeixyz789..."
```

### API Service Response

**resolve=cids:**
```json
{
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "ver": 2,
  "ts": "2024-01-15T11:00:00Z",
  "manifest_cid": "bafyreinew789...",
  "components": {
    "metadata": "bafybeinew456...",
    "image": "bafybeixyz789..."
  },
  "children_pi": ["01GX...", "01ABC..."]
}
```

**resolve=bytes:**
```json
{
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "ver": 2,
  "ts": "2024-01-15T11:00:00Z",
  "manifest_cid": "bafyreinew789...",
  "components": {
    "metadata": { "title": "Example", "description": "..." },
    "image": "<binary data or base64>"
  },
  "children_pi": ["01GX...", "01ABC..."]
}
```

---

## 5. Listing Versions (GET /entities/{pi}/versions)

**API Service Endpoint:** `GET /entities/{pi}/versions?limit=50&cursor=bafyrei...`
**Purpose:** Paginate through version history (newest → oldest).

### Algorithm

```javascript
async function listVersions(pi, limit = 50, cursor = null) {
  const versions = [];

  // Start from tip or cursor
  let currentCid;
  if (!cursor) {
    // Read tip
    const tip = await kuboCall('/files/read', {
      arg: `/arke/index/${pi.slice(0,2)}/${pi.slice(2,4)}/${pi}.tip`
    });
    currentCid = tip.trim();
  } else {
    currentCid = cursor;
  }

  // Walk backwards up to limit
  for (let i = 0; i < limit; i++) {
    // Fetch manifest
    const manifest = await kuboCall('/dag/get', { arg: currentCid });

    // Add to results
    versions.push({
      ver: manifest.ver,
      cid: currentCid,
      ts: manifest.ts,
      note: manifest.note
    });

    // Stop if no previous
    if (!manifest.prev) break;

    // Continue with previous
    currentCid = manifest.prev['/'];
  }

  // Next cursor is the last CID's prev (if exists)
  const lastManifest = versions[versions.length - 1];
  const nextCursor = lastManifest.prev ? lastManifest.prev['/'] : null;

  return { items: versions, next_cursor: nextCursor };
}
```

### Example Calls

**First page:**
```bash
# Read tip
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip"
# → bafyreinew789...

# Get manifest v2
curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=bafyreinew789..."
# → { ver: 2, prev: {"/": "bafyreimani123..."}, ... }

# Get manifest v1
curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=bafyreimani123..."
# → { ver: 1, prev: null, ... }
```

**Response:**
```json
{
  "items": [
    {
      "ver": 2,
      "cid": "bafyreinew789...",
      "ts": "2024-01-15T11:00:00Z",
      "note": "Updated metadata"
    },
    {
      "ver": 1,
      "cid": "bafyreimani123...",
      "ts": "2024-01-15T10:30:00Z",
      "note": "Initial version"
    }
  ],
  "next_cursor": null
}
```

---

## 6. Fetching Specific Versions

**API Service Endpoint:** `GET /entities/{pi}/versions/{selector}`
**Selector formats:** `ver:2` or `cid:bafyrei...`

### By CID

```bash
curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=bafyreimani123..."
```

### By Version Number

Walk from tip until version matches:

```javascript
async function getByVersion(pi, targetVer) {
  // Read tip
  const tip = await readTip(pi);
  let currentCid = tip;

  // Walk backwards
  while (true) {
    const manifest = await kuboCall('/dag/get', { arg: currentCid });

    if (manifest.ver === targetVer) {
      return { ...manifest, manifest_cid: currentCid };
    }

    if (!manifest.prev) {
      throw new Error('Version not found');
    }

    currentCid = manifest.prev['/'];
  }
}
```

---

## 7. Resolving PI to Tip (GET /resolve/{pi})

**API Service Endpoint:** `GET /resolve/{pi}`
**Purpose:** Fast lookup of current tip CID.

### Simple Tip Read

```bash
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/HQ/01HQZXY9M6K8N2P4R6T8V0W2.tip"
```

**Response:**
```
bafyreinew789...
```

### API Service Response

```json
{
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",
  "tip": "bafyreinew789..."
}
```

---

## Complete Example Flow

### Scenario: Upload image, create entity, update it

```bash
# 1. Upload image
curl -X POST \
  -F "file=@profile.jpg" \
  "http://localhost:5001/api/v0/add?cid-version=1&pin=false"
# → {"Hash":"bafybeimage123...","Size":"45678"}

# 2. Upload metadata
echo '{"name":"Alice","bio":"Engineer"}' | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/add?cid-version=1&pin=false"
# → {"Hash":"bafybemeta456...","Size":"42"}

# 3. Create manifest v1
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "schema": "arke/manifest/v1",
    "pi": "01ALICE00000000000000000",
    "ver": 1,
    "ts": "2024-01-15T12:00:00Z",
    "prev": null,
    "components": {
      "metadata": {"/": "bafybemeta456..."},
      "image": {"/": "bafybeimage123..."}
    },
    "children_pi": [],
    "note": "Initial profile"
  }' \
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true"
# → {"Cid":{"/":"bafyreiv1abc..."}}

# 4. Create directory
curl -X POST \
  "http://localhost:5001/api/v0/files/mkdir?arg=/arke/index/01/AL&parents=true"

# 5. Write .tip file
echo "bafyreiv1abc..." | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/arke/index/01/AL/01ALICE00000000000000000.tip&create=true&truncate=true"

# 6. Update metadata
echo '{"name":"Alice Smith","bio":"Senior Engineer"}' | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/add?cid-version=1&pin=false"
# → {"Hash":"bafybemetanew789...","Size":"52"}

# 7. Create manifest v2
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "schema": "arke/manifest/v1",
    "pi": "01ALICE00000000000000000",
    "ver": 2,
    "ts": "2024-01-15T13:00:00Z",
    "prev": {"/": "bafyreiv1abc..."},
    "components": {
      "metadata": {"/": "bafybemetanew789..."},
      "image": {"/": "bafybeimage123..."}
    },
    "children_pi": [],
    "note": "Updated name and title"
  }' \
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true"
# → {"Cid":{"/":"bafyreiv2xyz..."}}

# 8. Update .tip
echo "bafyreiv2xyz..." | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/arke/index/01/AL/01ALICE00000000000000000.tip&create=true&truncate=true"

# 9. Swap pins
curl -X POST \
  "http://localhost:5001/api/v0/pin/update?arg=bafyreiv1abc...&arg=bafyreiv2xyz..."

# 10. Fetch current entity
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/AL/01ALICE00000000000000000.tip"
# → bafyreiv2xyz...

curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=bafyreiv2xyz..."
# → {ver: 2, components: {...}, ...}
```

---

## Error Handling

### Common Errors

**404 - Not Found**
```bash
curl -X POST \
  "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/AL/NONEXISTENT.tip"
```
Response: HTTP 500 with error about file not existing

**Check before operation:**
```bash
# Use files/stat to check existence
curl -X POST \
  "http://localhost:5001/api/v0/files/stat?arg=/arke/index/01/AL/01ALICE00000000000000000.tip"
```

**409 - CAS Conflict**
```javascript
// Read tip
const currentTip = await readTip(pi);

// Check against expect_tip
if (currentTip !== expect_tip) {
  return {
    status: 409,
    error: "Conflict",
    message: "Tip has changed since last read",
    current_tip: currentTip,
    expected_tip: expect_tip
  };
}
```

**400 - Invalid CID**
```bash
# Bad CID format
curl -X POST \
  "http://localhost:5001/api/v0/dag/get?arg=invalid-cid"
# → Error response
```

**Validate CIDs:**
```javascript
function isValidCID(cidString) {
  return /^(Qm[1-9A-HJ-NP-Za-km-z]{44}|baf[a-z0-9]{50,})$/.test(cidString);
}
```

### Retry Logic

**Transient failures:**
```javascript
async function withRetry(fn, maxRetries = 3) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fn();
    } catch (error) {
      if (i === maxRetries - 1) throw error;
      await sleep(1000 * Math.pow(2, i)); // Exponential backoff
    }
  }
}
```

---

## Performance Tips

### 1. Batch Operations

Upload multiple components in one call:
```bash
curl -X POST \
  -F "file=@metadata.json" \
  -F "file=@image.png" \
  -F "file=@thumbnail.jpg" \
  "http://localhost:5001/api/v0/add?cid-version=1"
```

### 2. Use Pin Update

Always prefer `pin/update` over `pin/rm` + `pin/add`:
```bash
# Good ✓
curl -X POST "http://localhost:5001/api/v0/pin/update?arg=OLD_CID&arg=NEW_CID"

# Bad ✗
curl -X POST "http://localhost:5001/api/v0/pin/rm?arg=OLD_CID"
curl -X POST "http://localhost:5001/api/v0/pin/add?arg=NEW_CID"
```

### 3. Cache Tip Reads

The .tip file rarely changes, cache it:
```javascript
const tipCache = new Map();
const CACHE_TTL = 60000; // 1 minute

async function getCachedTip(pi) {
  const cached = tipCache.get(pi);
  if (cached && Date.now() - cached.time < CACHE_TTL) {
    return cached.cid;
  }

  const cid = await readTip(pi);
  tipCache.set(pi, { cid, time: Date.now() });
  return cid;
}
```

### 4. Lazy Component Loading

Don't fetch component bytes unless requested:
```javascript
// Return CIDs by default
GET /entities/{pi}?resolve=cids  // Fast

// Only fetch bytes when needed
GET /entities/{pi}?resolve=bytes&components=metadata  // Slower
```

---

## Language-Specific Examples

### Node.js/TypeScript

```typescript
import FormData from 'form-data';
import fs from 'fs';

const IPFS_API = 'http://localhost:5001/api/v0';

// Upload file
async function uploadFile(filePath: string): Promise<string> {
  const formData = new FormData();
  formData.append('file', fs.createReadStream(filePath));

  const response = await fetch(`${IPFS_API}/add?cid-version=1&pin=false`, {
    method: 'POST',
    body: formData
  });

  const data = await response.json();
  return data.Hash;
}

// Store manifest
async function storeManifest(manifest: any): Promise<string> {
  const response = await fetch(
    `${IPFS_API}/dag/put?store-codec=dag-cbor&input-codec=json&pin=true`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(manifest)
    }
  );

  const data = await response.json();
  return data.Cid['/'];
}

// Read tip
async function readTip(pi: string): Promise<string> {
  const dir = `/arke/index/${pi.slice(0,2)}/${pi.slice(2,4)}`;
  const response = await fetch(
    `${IPFS_API}/files/read?arg=${dir}/${pi}.tip`,
    { method: 'POST' }
  );

  return (await response.text()).trim();
}

// Write tip
async function writeTip(pi: string, cid: string): Promise<void> {
  const dir = `/arke/index/${pi.slice(0,2)}/${pi.slice(2,4)}`;

  // Create directory
  await fetch(
    `${IPFS_API}/files/mkdir?arg=${dir}&parents=true`,
    { method: 'POST' }
  );

  // Write file
  const formData = new FormData();
  formData.append('file', cid + '\n');

  await fetch(
    `${IPFS_API}/files/write?arg=${dir}/${pi}.tip&create=true&truncate=true`,
    {
      method: 'POST',
      body: formData
    }
  );
}
```

### Python

```python
import requests
import json

IPFS_API = "http://localhost:5001/api/v0"

def upload_file(file_path):
    """Upload file and return CID"""
    with open(file_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f"{IPFS_API}/add",
            files=files,
            params={'cid-version': 1, 'pin': False}
        )
        return response.json()['Hash']

def store_manifest(manifest):
    """Store manifest as DAG node"""
    response = requests.post(
        f"{IPFS_API}/dag/put",
        params={
            'store-codec': 'dag-cbor',
            'input-codec': 'json',
            'pin': True
        },
        json=manifest
    )
    return response.json()['Cid']['/']

def read_tip(pi):
    """Read current tip CID"""
    dir_path = f"/arke/index/{pi[:2]}/{pi[2:4]}"
    response = requests.post(
        f"{IPFS_API}/files/read",
        params={'arg': f"{dir_path}/{pi}.tip"}
    )
    return response.text.strip()

def write_tip(pi, cid):
    """Write tip file"""
    dir_path = f"/arke/index/{pi[:2]}/{pi[2:4]}"

    # Create directory
    requests.post(
        f"{IPFS_API}/files/mkdir",
        params={'arg': dir_path, 'parents': True}
    )

    # Write file
    files = {'file': (None, cid + '\n')}
    requests.post(
        f"{IPFS_API}/files/write",
        files=files,
        params={
            'arg': f"{dir_path}/{pi}.tip",
            'create': True,
            'truncate': True
        }
    )
```

---

## Testing

### Quick Test Script

```bash
#!/bin/bash

IPFS_API="http://localhost:5001/api/v0"

echo "1. Upload test file..."
TEST_CID=$(echo "test content" | curl -s -X POST -F "file=@-" \
  "$IPFS_API/add?cid-version=1" | jq -r .Hash)
echo "   CID: $TEST_CID"

echo "2. Create manifest..."
MANIFEST_CID=$(curl -s -X POST -H "Content-Type: application/json" \
  -d "{
    \"schema\": \"arke/manifest/v1\",
    \"pi\": \"01TEST0000000000000000\",
    \"ver\": 1,
    \"ts\": \"$(date -Iseconds)\",
    \"prev\": null,
    \"components\": {\"data\": {\"/\": \"$TEST_CID\"}},
    \"children_pi\": [],
    \"note\": \"Test\"
  }" \
  "$IPFS_API/dag/put?store-codec=dag-cbor&input-codec=json&pin=true" | jq -r '.Cid["/"]')
echo "   Manifest CID: $MANIFEST_CID"

echo "3. Create .tip file..."
curl -s -X POST "$IPFS_API/files/mkdir?arg=/arke/index/01/TE&parents=true"
echo "$MANIFEST_CID" | curl -s -X POST -F "file=@-" \
  "$IPFS_API/files/write?arg=/arke/index/01/TE/01TEST0000000000000000.tip&create=true&truncate=true"

echo "4. Read back .tip..."
TIP=$(curl -s -X POST "$IPFS_API/files/read?arg=/arke/index/01/TE/01TEST0000000000000000.tip")
echo "   Tip: $TIP"

echo "5. Fetch manifest..."
curl -s -X POST "$IPFS_API/dag/get?arg=$TIP" | jq .

echo "✓ Test complete!"
```

---

## Additional Resources

- **Kubo HTTP RPC API Docs**: https://docs.ipfs.tech/reference/kubo/rpc/
- **DAG-CBOR Spec**: https://ipld.io/specs/codecs/dag-cbor/spec/
- **CID Spec**: https://github.com/multiformats/cid
- **IPLD Spec**: https://ipld.io/

---

**Last Updated:** 2024-01-15
**Tested With:** Kubo v0.38.1
