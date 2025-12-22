# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a containerized IPFS (Kubo) node that serves as the foundational storage layer for the Arke API Service. It provides IPFS storage with a local datastore, using Kubo's HTTP RPC API for all operations. The system implements a versioned entity storage architecture using IPFS DAG structures and MFS (Mutable File System) for indexing.

## Architecture

The system has three main architectural layers:

1. **Kubo HTTP RPC API** (port 5001) - Direct API interface for IPFS operations
2. **MFS Index** - Directory structure at `/arke/index/{first-2-chars}/{next-2-chars}/{PI}.tip` that maps persistent identifiers (PIs) to manifest CIDs
3. **DAG Storage** - CBOR-encoded manifests with IPLD links that form versioned chains via `prev` pointers

### Data Model

**Manifests** are the core data structure, stored as `dag-cbor` with this schema:
```json
{
  "schema": "arke/manifest/v1",
  "pi": "01HQZXY9M6K8N2P4R6T8V0W2",  // ULID identifier
  "ver": 2,                           // Version number (increments)
  "ts": "2024-01-15T11:00:00Z",
  "prev": {"/": "bafyrei..."},        // IPLD link to previous version
  "components": {                      // Named CID references
    "metadata": {"/": "bafybei..."},
    "image": {"/": "bafybei..."}
  },
  "children_pi": ["01GX...", "01GZ..."],
  "note": "Version description"
}
```

**Tip files** (`.tip`) are small text files in MFS that contain the CID of the latest manifest for each PI. They enable O(1) lookup of current state without walking the version chain.

**Directory sharding**: PIs are sharded into directories using the first 4 characters (e.g., PI `01K75GZSKKSP2K6TP05JBFNV09` → `/arke/index/01/K7/`).

## Development Commands

### Start/Stop Kubo Node

```bash
# Local development
docker compose up -d
docker compose down

# Production deployment
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml down

# View logs
docker compose logs -f
```

### Common IPFS Operations

All Kubo RPC operations use POST requests to `http://localhost:5001/api/v0/...`

```bash
# Check node status
curl -X POST http://localhost:5001/api/v0/version

# Add content (returns CID)
curl -X POST -F "file=@myfile.txt" "http://localhost:5001/api/v0/add?cid-version=1&pin=false"

# Store manifest as DAG
curl -X POST -H "Content-Type: application/json" -d '{}' \
  "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true"

# Retrieve manifest
curl -X POST "http://localhost:5001/api/v0/dag/get?arg=bafyrei..."

# Read .tip file from MFS
curl -X POST "http://localhost:5001/api/v0/files/read?arg=/arke/index/01/K7/01K75....tip"

# Write .tip file to MFS
echo "bafyrei..." | curl -X POST -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/arke/index/01/K7/01K75....tip&create=true&truncate=true"
```

## Disaster Recovery System

The repository includes a complete CAR-based backup and restore system centered around **snapshot indexes**.

### Snapshot Architecture

Snapshots are dag-json objects that capture the entire system state:
```json
{
  "schema": "arke/snapshot-index@v1",
  "seq": 42,
  "ts": "2025-10-09T23:00:00Z",
  "prev": {"/": "baguqee..."},  // Link to previous snapshot
  "entries": [
    {"pi": "01K75...", "ver": 2, "tip": {"/": "baguqee..."}}
  ]
}
```

**Critical**: Snapshots MUST be stored as `dag-json` (not `dag-cbor`) because CAR exporters require dag-json to properly follow IPLD links. The Kubo CLI must be used (`ipfs dag put --store-codec=dag-json`) instead of the HTTP API.

### DR Scripts

Located in `scripts/`:

```bash
# Create snapshot index from current MFS state
./scripts/build-snapshot.sh

# Export snapshot to portable CAR file
./scripts/export-car.sh

# Restore from CAR on fresh node (tested with complete data destruction)
./scripts/restore-from-car.sh <car-file> [snapshot-cid]

# Verify entity is fully accessible
./scripts/verify-entity.sh <PI>
```

**Important**: The restore script has been thoroughly tested with the "nuclear option" (complete data destruction). All logging goes to stderr to avoid contaminating function return values.

### Backup Files Structure

- `snapshots/` - Snapshot metadata JSON files
- `snapshots/latest.json` - Symlink to most recent snapshot
- `backups/` - CAR files named `arke-{seq}-{timestamp}.car`

## Key Implementation Details

### CAS (Compare-And-Swap) for Updates

When adding versions, always implement CAS to prevent conflicts:
1. Read current tip CID
2. Verify it matches `expect_tip` from request
3. Return 409 Conflict if mismatch
4. Proceed with update only if match

### Pin Management

- **Components**: Upload with `pin=false` (transient)
- **Manifests**: Always `pin=true` when storing
- **Updates**: Use `pin/update` (not `pin/rm` + `pin/add`) for atomic swaps and efficiency

### Version Chain Walking

To list versions or find specific version numbers:
1. Start from tip CID (read from `.tip` file)
2. Fetch manifest at current CID
3. Follow `prev` links backward
4. Stop when `prev` is null or limit reached

### Directory Creation

Always use `parents=true` when creating MFS directories:
```bash
curl -X POST "http://localhost:5001/api/v0/files/mkdir?arg=/arke/index/01/K7&parents=true"
```

## Private Network Mode

This node operates as a **private storage backend** with public IPFS network disabled. Configuration is automated via `ipfs-init.d/001-private-mode.sh` which runs on every container startup.

Key settings applied:
- `Routing.Type: none` - No DHT participation
- `Bootstrap: []` - No public peer discovery
- Port 4001 bound to `127.0.0.1` - No external swarm connections
- All relay/NAT services disabled

This reduces memory from ~1GB to ~50-170MB and eliminates DHT routing overhead. See `CAPACITY.md` for details.

## Port Configuration

- **4001**: P2P (Swarm) - Localhost only (private mode)
- **5001**: HTTP RPC API - Localhost only
- **8080**: HTTP Gateway - Not exposed (accessed via nginx)

In production, all ports are bound to `127.0.0.1`. External access goes through nginx reverse proxy.

## Resource Limits

Production deployment includes:
- CPU: 1-2 cores (reservations-limits)
- Memory: 1-2 GB
- Log rotation: 10MB max size, 3 files
- Profile: `server` (optimized for production)

## Testing and Verification

### Health Checks

The container includes automatic health checks:
```bash
ipfs id || exit 1  # Runs every 30s
```

Manual verification:
```bash
# Container health
docker compose ps

# Repo stats
curl -X POST http://localhost:5001/api/v0/repo/stat | jq .

# Connected peers
curl -X POST http://localhost:5001/api/v0/swarm/peers | jq .
```

### DR Testing

See `DR_TEST.md` for complete nuclear test procedure (verified working as of 2025-10-10).

## Important Files

- `README.md` - Deployment guide and basic operations
- `API_WALKTHROUGH.md` - Complete guide to implementing Arke API endpoints using Kubo RPC
- `DISASTER_RECOVERY.md` - Full DR strategy and procedures
- `DR_TEST.md` - Step-by-step nuclear DR test
- `CAPACITY.md` - Private network config and capacity limits
- `MONITORING.md` - Automated maintenance and alerting
- `docker-compose.yml` - Local development configuration
- `docker-compose.nginx.yml` - Production configuration with nginx reverse proxy
- `ipfs-init.d/001-private-mode.sh` - Configures IPFS for offline/private mode on startup

## Common Pitfalls

1. **Never use dag-cbor for snapshots** - Must be dag-json for CAR export link following
2. **Always use Kubo CLI for dag-json operations** - HTTP API doesn't preserve IPLD link semantics correctly
3. **Check CAS before updates** - Read tip, verify expect_tip matches, or return 409
4. **Use pin/update not separate rm+add** - More efficient, atomic operation
5. **All Kubo RPC calls use POST** - Even for read operations like dag/get or files/read
