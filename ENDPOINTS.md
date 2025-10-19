# Endpoint Documentation

This document explains the two public endpoints and their purposes.

## Overview

The deployment provides two distinct endpoints:

1. **`https://ipfs-api.arke.institute`** - API operations and RPC calls
2. **`https://ipfs.arke.institute`** - Content retrieval (Gateway)

---

## 1. API Endpoint: `https://ipfs-api.arke.institute`

**Purpose**: Perform operations on IPFS and access the Arke API service.

**Routes to**:
- IPFS Kubo RPC API (port 5001) - All `/api/v0/*` paths
- Arke API Service (port 3000) - `/events`, `/snapshot`, `/index-pointer`, `/health`

### Use Cases

#### A. Arke API Service Operations

High-level REST API for managing the Arke entity system.

**Health Check**
```bash
curl https://ipfs-api.arke.institute/health
# → {"status":"healthy"}
```

**Event Stream** (get create/update events)
```bash
curl 'https://ipfs-api.arke.institute/events?limit=50'
# → {"items":[...], "total_events":7, "total_pis":6, "has_more":true, "next_cursor":"bafyrei..."}
```

**Append Event** (record entity changes)
```bash
curl -X POST https://ipfs-api.arke.institute/events/append \
  -H 'Content-Type: application/json' \
  -d '{"type":"create","pi":"01ABC...","ver":1,"tip_cid":"bafyrei..."}'
# → {"event_cid":"bafyrei...","success":true}
```

**Get Index Pointer** (system state)
```bash
curl https://ipfs-api.arke.institute/index-pointer
# → {"schema":"arke/index-pointer@v2","event_head":"bafyrei...","event_count":7,"total_count":6,...}
```

**Get Latest Snapshot** (bulk entity list)
```bash
curl https://ipfs-api.arke.institute/snapshot/latest
# → {"schema":"arke/snapshot@v1","entries":[...]}
```

---

#### B. IPFS Kubo RPC Operations

Low-level IPFS operations for storage and DAG manipulation.

**Upload Files** (returns CID)
```bash
curl -X POST "https://ipfs-api.arke.institute/api/v0/add?cid-version=1&pin=false" \
  -F "file=@image.png"
# → {"Hash":"bafybei...","Size":"12345"}
```

**Store DAG Node** (manifests)
```bash
echo '{"schema":"arke/manifest/v1","pi":"01ABC...","ver":1,...}' | \
  curl -X POST "https://ipfs-api.arke.institute/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true" \
  -F "object data=@-"
# → {"Cid":{"/":"bafyrei..."}}
```

**Retrieve DAG Node** (read manifest)
```bash
curl -X POST 'https://ipfs-api.arke.institute/api/v0/dag/get?arg=bafyrei...'
# → {"schema":"arke/manifest/v1","pi":"01ABC...","ver":1,...}
```

**MFS Operations** (file system for .tip files)
```bash
# Create directory
curl -X POST 'https://ipfs-api.arke.institute/api/v0/files/mkdir?arg=/arke/index/01/AB&parents=true'

# Write .tip file
echo -n "bafyrei..." | \
  curl -X POST "https://ipfs-api.arke.institute/api/v0/files/write?arg=/arke/index/01/AB/01ABC.tip&create=true&truncate=true" \
  -F "file=@-"

# Read .tip file
curl -X POST 'https://ipfs-api.arke.institute/api/v0/files/read?arg=/arke/index/01/AB/01ABC.tip'
# → bafyrei...
```

**Pin Management**
```bash
# Pin content
curl -X POST 'https://ipfs-api.arke.institute/api/v0/pin/add?arg=bafyrei...'

# Update pin (atomic swap)
curl -X POST 'https://ipfs-api.arke.institute/api/v0/pin/update?arg=OLD_CID&arg=NEW_CID'

# Remove pin
curl -X POST 'https://ipfs-api.arke.institute/api/v0/pin/rm?arg=bafyrei...'
```

**System Info**
```bash
# Get IPFS version
curl -X POST https://ipfs-api.arke.institute/api/v0/version
# → {"Version":"0.38.1","Commit":"6bf52ae","Repo":"18","System":"amd64/linux","Golang":"go1.25.2"}

# Get repo stats
curl -X POST https://ipfs-api.arke.institute/api/v0/repo/stat
# → {"RepoSize":11792193,"StorageMax":10000000000,"NumObjects":123,"RepoPath":"/data/ipfs",...}

# List peers
curl -X POST https://ipfs-api.arke.institute/api/v0/swarm/peers
# → {"Peers":[...]}
```

### Summary: When to Use API Endpoint

**Use `https://ipfs-api.arke.institute` for:**
- ✅ Uploading files (`/api/v0/add`)
- ✅ Creating/updating entities (DAG put, MFS operations)
- ✅ Querying events and snapshots (`/events`, `/snapshot/latest`)
- ✅ Managing pins (`/api/v0/pin/*`)
- ✅ Any write operations
- ✅ System administration (version, stats, peers)

---

## 2. Gateway Endpoint: `https://ipfs.arke.institute`

**Purpose**: Retrieve and browse content by CID (read-only).

**Routes to**: IPFS Gateway (port 8080)

### Use Cases

#### Content Retrieval by CID

**Retrieve File/Object**
```bash
curl https://ipfs.arke.institute/ipfs/bafybeiemxf5abjwjbikoz4mc3a3dla6ual3jsgpdr4cjr3oz3evfyavhwq
# → (file contents)
```

**Retrieve with Filename**
```bash
curl https://ipfs.arke.institute/ipfs/bafybei.../image.png
# → Downloads as image.png
```

**Browse Directory Listing** (if CID is a directory)
```bash
curl https://ipfs.arke.institute/ipfs/bafybei.../
# → HTML directory listing
```

**Retrieve Specific File from Directory**
```bash
curl https://ipfs.arke.institute/ipfs/bafybei.../path/to/file.json
# → file.json contents
```

#### Component Retrieval

Get entity components (images, metadata, etc.) referenced in manifests:

```bash
# Example: Manifest has component CID bafybei...
curl https://ipfs.arke.institute/ipfs/bafybei...
# → (component data - could be JSON, image, video, etc.)
```

#### Browser Access

The gateway serves content in browser-friendly formats:

```
https://ipfs.arke.institute/ipfs/bafybei.../image.png
```
- Browser displays image directly
- Right-click to save
- Shareable link for public content

### Gateway vs API for Retrieval

**Gateway** (`/ipfs/{cid}`):
- ✅ Browser-friendly (HTML, images, videos render)
- ✅ Clean URLs for sharing
- ✅ HTTP GET (cacheable)
- ✅ CORS enabled
- ❌ Read-only

**API** (`/api/v0/cat?arg={cid}`):
- ✅ Programmatic access
- ✅ Returns raw bytes
- ✅ Works for all content types
- ❌ Requires POST requests
- ❌ Less browser-friendly

### Summary: When to Use Gateway Endpoint

**Use `https://ipfs.arke.institute` for:**
- ✅ Retrieving component files (images, metadata, videos)
- ✅ Sharing public content (clean URLs)
- ✅ Browser access to content
- ✅ Downloading files by CID
- ✅ Read-only content retrieval

---

## URL Routing Summary

### `ipfs-api.arke.institute` Routes:

| Path Pattern | Destination | Purpose |
|-------------|-------------|---------|
| `/health` | API Service (3000) | Health check |
| `/events` | API Service (3000) | Event stream |
| `/events/append` | API Service (3000) | Append events |
| `/snapshot/latest` | API Service (3000) | Get snapshot |
| `/index-pointer` | API Service (3000) | System state |
| `/api/v0/*` | IPFS RPC (5001) | All IPFS operations |

### `ipfs.arke.institute` Routes:

| Path Pattern | Destination | Purpose |
|-------------|-------------|---------|
| `/ipfs/{cid}` | IPFS Gateway (8080) | Content retrieval |
| `/ipfs/{cid}/path` | IPFS Gateway (8080) | Directory/file access |

---

## Complete Workflow Example

### Creating and Retrieving an Entity

```bash
# 1. Upload component (API endpoint)
curl -X POST "https://ipfs-api.arke.institute/api/v0/add?cid-version=1&pin=false" \
  -F "file=@metadata.json"
# → {"Hash":"bafybeimeta123..."}

# 2. Create manifest (API endpoint)
echo '{
  "schema":"arke/manifest/v1",
  "pi":"01EXAMPLE000000000000",
  "ver":1,
  "ts":"2025-10-19T12:00:00Z",
  "prev":null,
  "components":{"metadata":{"/":"bafybeimeta123..."}},
  "children_pi":[],
  "note":"Test"
}' | curl -X POST "https://ipfs-api.arke.institute/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true" \
  -F "object data=@-"
# → {"Cid":{"/":"bafyreimanifest456..."}}

# 3. Write .tip file (API endpoint)
echo -n "bafyreimanifest456..." | \
  curl -X POST "https://ipfs-api.arke.institute/api/v0/files/write?arg=/arke/index/01/EX/01EXAMPLE000000000000.tip&create=true&truncate=true" \
  -F "file=@-"

# 4. Record event (API endpoint)
curl -X POST https://ipfs-api.arke.institute/events/append \
  -H 'Content-Type: application/json' \
  -d '{
    "type":"create",
    "pi":"01EXAMPLE000000000000",
    "ver":1,
    "tip_cid":"bafyreimanifest456..."
  }'
# → {"event_cid":"bafyreievent789...","success":true}

# 5. Retrieve manifest (API endpoint)
curl -X POST 'https://ipfs-api.arke.institute/api/v0/dag/get?arg=bafyreimanifest456...'
# → {manifest object}

# 6. Retrieve component (Gateway endpoint)
curl https://ipfs.arke.institute/ipfs/bafybeimeta123...
# → {metadata JSON}
```

---

## Rate Limiting

Both endpoints have rate limiting configured (MVP testing values):

**API Endpoint** (`ipfs-api.arke.institute`):
- 1000 requests/second per IP
- Burst: 2000 requests
- Max connections: 200

**Gateway Endpoint** (`ipfs.arke.institute`):
- 500 requests/second per IP
- Burst: 1000 requests
- Max connections: 100

---

## Security Headers

Both endpoints include:
- `X-Frame-Options: DENY` (API) / `SAMEORIGIN` (Gateway)
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`

---

## Quick Reference

**Need to upload or modify?** → `ipfs-api.arke.institute`
**Need to download or view?** → `ipfs.arke.institute`

**Working with Arke entities?** → `ipfs-api.arke.institute`
**Sharing content links?** → `ipfs.arke.institute`

**Programmatic operations?** → `ipfs-api.arke.institute`
**Browser-friendly access?** → `ipfs.arke.institute`

---

## Testing

A comprehensive test suite is available to verify all endpoints and operations:

```bash
# Test production deployment
./tests/test-deployment.sh

# Test and clean up test data
./tests/test-deployment.sh --cleanup
```

See `tests/README.md` for complete testing documentation.
