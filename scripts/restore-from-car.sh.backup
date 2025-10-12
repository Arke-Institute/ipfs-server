#!/bin/bash
# Restore from CAR file to a fresh IPFS node
# Imports CAR, reads snapshot index, rebuilds all .tip files in MFS

set -euo pipefail

IPFS_API="${IPFS_API:-http://localhost:5001/api/v0}"
CONTAINER_NAME="${CONTAINER_NAME:-ipfs-node}"
INDEX_ROOT="/arke/index"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# All logging functions output to stderr to avoid contaminating function returns
log() { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*" >&2; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Usage
usage() {
  echo "Usage: $0 <car-file> [snapshot-cid]"
  echo ""
  echo "Arguments:"
  echo "  car-file      Path to the CAR file to restore from"
  echo "  snapshot-cid  (Optional) CID of the snapshot object"
  echo "                If omitted, will try to read from metadata file"
  echo ""
  echo "Examples:"
  echo "  $0 backups/arke-1-20251009-123456.car"
  echo "  $0 backups/arke-1-20251009-123456.car bafyrei..."
  exit 1
}

# Import CAR file
import_car() {
  local car_file="$1"

  if [[ ! -f "$car_file" ]]; then
    error "CAR file not found: $car_file"
  fi

  local car_basename=$(basename "$car_file")
  local container_path="/tmp/$car_basename"

  log "Importing CAR file: $car_basename"

  # Copy CAR file into container
  log "Copying CAR file to container..."
  docker cp "$car_file" "${CONTAINER_NAME}:${container_path}"

  # Import with stats
  log "Running: ipfs dag import $container_path"
  if docker exec "$CONTAINER_NAME" ipfs dag import --stats "$container_path" 2>&1 | tee /tmp/import.log; then
    success "CAR file imported successfully"
  else
    error "Failed to import CAR file"
  fi

  # Clean up
  docker exec "$CONTAINER_NAME" rm "$container_path" 2>/dev/null || true

  # Extract stats
  local blocks=$(grep -oP 'imported \K\d+' /tmp/import.log 2>/dev/null || echo "unknown")
  local bytes=$(grep -oP 'bytes \K\d+' /tmp/import.log 2>/dev/null || echo "unknown")

  success "Imported $blocks blocks ($bytes bytes)"
}

# Find snapshot CID from metadata file
find_snapshot_cid_from_metadata() {
  local car_file="$1"
  local metadata_file="${car_file%.car}.json"

  if [[ -f "$metadata_file" ]]; then
    local cid=$(jq -r '.snapshot_cid // empty' "$metadata_file")
    if [[ -n "$cid" ]]; then
      log "Found snapshot CID in metadata: $cid"
      echo "$cid"
      return 0
    fi
  fi

  return 1
}

# Read snapshot object
get_snapshot() {
  local snapshot_cid="$1"

  log "Fetching snapshot object: $snapshot_cid"

  # Use -sS instead of -sf to see errors
  local snapshot=$(curl -sS -X POST "$IPFS_API/dag/get?arg=$snapshot_cid" 2>&1)

  if [[ -z "$snapshot" ]]; then
    error "Failed to fetch snapshot object (empty response)"
  fi

  # Check if response is an error
  if echo "$snapshot" | grep -q '"Type":"error"'; then
    local msg=$(echo "$snapshot" | jq -r '.Message // "Unknown error"')
    error "IPFS error fetching snapshot: $msg"
  fi

  # Validate it's valid JSON
  if ! echo "$snapshot" | jq empty 2>/dev/null; then
    error "Invalid JSON response from IPFS: $snapshot"
  fi

  local schema=$(echo "$snapshot" | jq -r '.schema // ""')
  if [[ "$schema" != "arke/snapshot-index@v1" ]]; then
    error "Invalid snapshot schema: $schema (expected arke/snapshot-index@v1)"
  fi

  echo "$snapshot"
}

# Compute shard path from PI
shard_path() {
  local pi="$1"
  local shard1="${pi:0:2}"
  local shard2="${pi:2:2}"
  echo "$INDEX_ROOT/$shard1/$shard2"
}

# Create .tip file in MFS
create_tip_file() {
  local pi="$1"
  local tip_cid="$2"

  local dir=$(shard_path "$pi")
  local tip_path="$dir/${pi}.tip"

  # Create directory
  curl -sf -X POST "$IPFS_API/files/mkdir?arg=$dir&parents=true" > /dev/null || true

  # Write tip file (CID + newline)
  echo "$tip_cid" | curl -sf -X POST \
    -F "file=@-" \
    "$IPFS_API/files/write?arg=$tip_path&create=true&truncate=true" > /dev/null

  if [[ $? -eq 0 ]]; then
    log "  ✓ Created: $tip_path → $tip_cid"
  else
    error "  ✗ Failed to create: $tip_path"
  fi
}

# Rebuild MFS from snapshot
rebuild_mfs() {
  local snapshot="$1"

  log "Rebuilding MFS structure from snapshot..."

  # Use array indexing instead of streaming (more reliable)
  local count=$(echo "$snapshot" | jq -r '.entries | length')
  local seq=$(echo "$snapshot" | jq -r '.seq')
  local ts=$(echo "$snapshot" | jq -r '.ts')

  # Validate we got data
  if [[ -z "$count" ]] || [[ "$count" == "null" ]] || [[ "$count" -eq 0 ]]; then
    error "Invalid snapshot: no entries found"
  fi

  log "Snapshot sequence: $seq"
  log "Snapshot timestamp: $ts"
  log "Total entities: $count"

  echo ""

  # Loop using array indices (avoids streaming issues)
  for i in $(seq 0 $((count - 1))); do
    local pi=$(echo "$snapshot" | jq -r ".entries[$i].pi")
    local tip_cid=$(echo "$snapshot" | jq -r ".entries[$i].tip[\"/\"]")
    local ver=$(echo "$snapshot" | jq -r ".entries[$i].ver")

    # Validate we got real values
    if [[ -z "$pi" ]] || [[ "$pi" == "null" ]]; then
      warn "Skipping entry $i: invalid PI"
      continue
    fi

    if [[ -z "$tip_cid" ]] || [[ "$tip_cid" == "null" ]]; then
      warn "Skipping entry $i ($pi): invalid tip CID"
      continue
    fi

    log "[$((i+1))/$count] $pi (v$ver)"
    create_tip_file "$pi" "$tip_cid"
  done

  echo ""
  success "Rebuilt $count .tip files in MFS"
}

# Verify restoration
verify_restoration() {
  local snapshot="$1"

  log "Verifying restoration..."

  local count=$(echo "$snapshot" | jq -r '.entries | length')
  local errors=0

  # Use array indexing for verification too
  for i in $(seq 0 $((count - 1))); do
    local pi=$(echo "$snapshot" | jq -r ".entries[$i].pi")
    local expected_tip=$(echo "$snapshot" | jq -r ".entries[$i].tip[\"/\"]")

    # Skip if invalid
    if [[ -z "$pi" ]] || [[ "$pi" == "null" ]]; then
      continue
    fi

    local dir=$(shard_path "$pi")
    local tip_path="$dir/${pi}.tip"

    # Read tip file
    local actual_tip=$(curl -sf -X POST "$IPFS_API/files/read?arg=$tip_path" | tr -d '\n')

    if [[ "$actual_tip" != "$expected_tip" ]]; then
      warn "Mismatch for $pi: expected $expected_tip, got $actual_tip"
      errors=$((errors + 1))
    fi
  done

  if [[ $errors -eq 0 ]]; then
    success "All .tip files verified successfully ✓"
    return 0
  else
    error "$errors verification errors found"
    return 1
  fi
}

# Main
main() {
  if [[ $# -lt 1 ]]; then
    usage
  fi

  local car_file="$1"
  local snapshot_cid="${2:-}"

  log "Starting CAR restoration..."
  log "CAR file: $car_file"

  # Check if docker container is running
  if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    error "Docker container '$CONTAINER_NAME' is not running"
  fi

  # Check if IPFS is accessible
  if ! curl -sf -X POST "$IPFS_API/version" > /dev/null; then
    error "Cannot connect to IPFS at $IPFS_API"
  fi

  # Import CAR
  import_car "$car_file"

  # Determine snapshot CID
  if [[ -z "$snapshot_cid" ]]; then
    log "No snapshot CID provided, checking metadata..."
    snapshot_cid=$(find_snapshot_cid_from_metadata "$car_file") || {
      error "Could not determine snapshot CID. Please provide it as second argument."
    }
  fi

  log "Snapshot CID: $snapshot_cid"

  # Get snapshot object
  local snapshot=$(get_snapshot "$snapshot_cid")

  # Rebuild MFS
  rebuild_mfs "$snapshot"

  # Verify
  verify_restoration "$snapshot"

  # Output summary
  local count=$(echo "$snapshot" | jq '.entries | length')
  local seq=$(echo "$snapshot" | jq -r '.seq')

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo -e "${GREEN}Restoration Complete${NC}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Snapshot:   $snapshot_cid (seq $seq)"
  echo "Entities:   $count"
  echo "MFS:        $INDEX_ROOT"
  echo "Status:     ✓ All verified"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  success "System restored from CAR file! Ready to serve requests."
}

main "$@"
