#!/bin/bash
# Export snapshot to CAR (Content Addressable aRchive) file
# Uses the snapshot index CID as the single root

set -euo pipefail

SNAPSHOTS_DIR="${SNAPSHOTS_DIR:-./snapshots}"
BACKUPS_DIR="${BACKUPS_DIR:-./backups}"
CONTAINER_NAME="${CONTAINER_NAME:-ipfs-node}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# Get latest snapshot metadata
get_latest_snapshot() {
  if [[ ! -f "$SNAPSHOTS_DIR/latest.json" ]]; then
    error "No snapshot found. Run build-snapshot.sh first."
  fi

  jq -r '.cid' "$SNAPSHOTS_DIR/latest.json"
}

# Export CAR file
export_car() {
  local snapshot_cid="$1"
  local seq=$(jq -r '.seq' "$SNAPSHOTS_DIR/latest.json")
  local timestamp=$(date -u +"%Y%m%d-%H%M%S")

  local car_filename="arke-${seq}-${timestamp}.car"
  local car_path="$BACKUPS_DIR/$car_filename"

  log "Exporting snapshot to CAR..."
  log "Snapshot CID: $snapshot_cid"
  log "Sequence:     $seq"
  log "Output:       $car_path"

  # Create backups directory
  mkdir -p "$BACKUPS_DIR"

  # Export using docker exec (since we're running in a container)
  log "Running: ipfs dag export $snapshot_cid"

  if docker exec "$CONTAINER_NAME" ipfs dag export "$snapshot_cid" > "$car_path"; then
    success "CAR file exported successfully"
  else
    error "Failed to export CAR file"
  fi

  # Get file size
  local size_bytes=$(stat -f%z "$car_path" 2>/dev/null || stat -c%s "$car_path" 2>/dev/null || echo "0")
  local size_mb=$(echo "scale=2; $size_bytes / 1048576" | bc)

  success "CAR file size: ${size_mb} MB ($size_bytes bytes)"

  # Verify it's a valid CAR file (check magic bytes)
  local magic=$(xxd -p -l 4 "$car_path" 2>/dev/null || echo "")
  if [[ -n "$magic" ]]; then
    log "CAR file magic bytes: $magic"
  fi

  # Save export metadata
  local metadata=$(jq -n \
    --arg snapshot_cid "$snapshot_cid" \
    --argjson seq "$seq" \
    --arg timestamp "$timestamp" \
    --arg filename "$car_filename" \
    --arg path "$car_path" \
    --argjson size "$size_bytes" \
    '{
      snapshot_cid: $snapshot_cid,
      seq: $seq,
      timestamp: $timestamp,
      filename: $filename,
      path: $path,
      size_bytes: $size
    }')

  echo "$metadata" > "$BACKUPS_DIR/${car_filename%.car}.json"

  # Output summary
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo -e "${GREEN}CAR Export Complete${NC}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Snapshot:  $snapshot_cid"
  echo "Sequence:  $seq"
  echo "File:      $car_filename"
  echo "Size:      ${size_mb} MB"
  echo "Location:  $car_path"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  # Verification reminder
  warn "Next steps:"
  echo "  1. Verify the CAR file integrity"
  echo "  2. Copy to offsite storage"
  echo "  3. Test restore on a fresh node"
  echo ""

  echo "$car_path"
}

# Main
main() {
  log "Starting CAR export..."

  # Check if docker container is running
  if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    error "Docker container '$CONTAINER_NAME' is not running"
  fi

  # Get latest snapshot
  local snapshot_cid=$(get_latest_snapshot)

  if [[ -z "$snapshot_cid" || "$snapshot_cid" == "null" ]]; then
    error "Invalid snapshot CID"
  fi

  export_car "$snapshot_cid"
}

main "$@"
