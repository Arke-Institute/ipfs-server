#!/bin/bash
# Build snapshot index from all .tip files in MFS
# Creates a dag-cbor object that serves as the CAR export root

set -euo pipefail

IPFS_API="${IPFS_API:-http://localhost:5001/api/v0}"
CONTAINER_NAME="${CONTAINER_NAME:-ipfs-node}"
INDEX_ROOT="/arke/index"
SNAPSHOTS_DIR="${SNAPSHOTS_DIR:-./snapshots}"
PREV_SNAPSHOT=""

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

# Get sequence number from previous snapshot
get_next_seq() {
  if [[ -f "$SNAPSHOTS_DIR/latest.json" ]]; then
    local prev_seq=$(jq -r '.seq // 0' "$SNAPSHOTS_DIR/latest.json" 2>/dev/null || echo "0")
    PREV_SNAPSHOT=$(jq -r '.cid // null' "$SNAPSHOTS_DIR/latest.json" 2>/dev/null || echo "null")
    echo $((prev_seq + 1))
  else
    echo 1
  fi
}

# Recursively find all .tip files in MFS
find_tip_files() {
  local path="$1"
  local tips=()

  # List directory
  local entries=$(curl -sf -X POST "$IPFS_API/files/ls?arg=$path&long=true" || echo '{"Entries":[]}')

  # Parse entries
  echo "$entries" | jq -c '.Entries[]?' | while read -r entry; do
    local name=$(echo "$entry" | jq -r '.Name')
    local type=$(echo "$entry" | jq -r '.Type')
    local full_path="$path/$name"

    if [[ $type -eq 1 ]]; then
      # Directory - recurse
      find_tip_files "$full_path"
    elif [[ $name == *.tip ]]; then
      # Tip file - output path
      echo "$full_path"
    fi
  done
}

# Read a tip file and get manifest CID
read_tip() {
  local tip_path="$1"
  curl -sf -X POST "$IPFS_API/files/read?arg=$tip_path" | tr -d '\n' || echo ""
}

# Get manifest from CID
get_manifest() {
  local cid="$1"
  curl -sf -X POST "$IPFS_API/dag/get?arg=$cid" || echo "{}"
}

# Extract PI from tip file path
extract_pi() {
  local tip_path="$1"
  basename "$tip_path" .tip
}

# Build snapshot index
build_snapshot() {
  log "Building snapshot index..."

  # Get next sequence number
  local seq=$(get_next_seq)
  local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  log "Snapshot sequence: $seq"
  log "Timestamp: $timestamp"

  # Find all tip files
  log "Scanning $INDEX_ROOT for .tip files..."

  # Portable alternative to mapfile (works on bash 3+)
  local tip_files_temp="/tmp/tip_files_$$.txt"
  find_tip_files "$INDEX_ROOT" > "$tip_files_temp"

  local count=$(wc -l < "$tip_files_temp" | tr -d ' ')
  if [[ $count -eq 0 ]]; then
    rm -f "$tip_files_temp"
    error "No .tip files found in $INDEX_ROOT"
  fi

  success "Found $count .tip files"

  # Build entries array
  local entries="[]"
  local i=0

  while IFS= read -r tip_path; do
    i=$((i + 1))
    local pi=$(extract_pi "$tip_path")

    log "[$i/$count] Processing $pi..."

    # Read tip CID
    local tip_cid=$(read_tip "$tip_path")
    if [[ -z "$tip_cid" ]]; then
      warn "Failed to read tip for $pi, skipping"
      continue
    fi

    # Get manifest to extract version
    local manifest=$(get_manifest "$tip_cid")
    local ver=$(echo "$manifest" | jq -r '.ver // 0')

    if [[ $ver -eq 0 ]]; then
      warn "Failed to get manifest for $pi (CID: $tip_cid), skipping"
      continue
    fi

    log "  PI: $pi | Ver: $ver | Tip: $tip_cid"

    # Add entry
    local entry=$(jq -n \
      --arg pi "$pi" \
      --argjson ver "$ver" \
      --arg tip "$tip_cid" \
      '{pi: $pi, ver: $ver, tip: {"/": $tip}}')

    entries=$(echo "$entries" | jq --argjson entry "$entry" '. += [$entry]')
  done < "$tip_files_temp"

  # Clean up temp file
  rm -f "$tip_files_temp"

  local final_count=$(echo "$entries" | jq 'length')
  success "Collected $final_count entries"

  # Build snapshot object
  local prev_link="null"
  if [[ "$PREV_SNAPSHOT" != "null" && -n "$PREV_SNAPSHOT" ]]; then
    prev_link=$(jq -n --arg cid "$PREV_SNAPSHOT" '{"/": $cid}')
    log "Linking to previous snapshot: $PREV_SNAPSHOT"
  fi

  local snapshot=$(jq -n \
    --arg schema "arke/snapshot-index@v1" \
    --argjson seq "$seq" \
    --arg ts "$timestamp" \
    --argjson prev "$prev_link" \
    --argjson entries "$entries" \
    '{schema: $schema, seq: $seq, ts: $ts, prev: $prev, entries: $entries}')

  log "Snapshot object created ($(echo "$snapshot" | jq -c . | wc -c) bytes)"

  # Store as dag-json (preserves IPLD links for CAR export)
  log "Storing snapshot as dag-json..."
  # Use CLI instead of HTTP API because it properly handles IPLD links
  local snapshot_cid=$(echo "$snapshot" | docker exec -i "$CONTAINER_NAME" \
    ipfs dag put --store-codec=dag-json --input-codec=json --pin=true)

  # Remove any whitespace
  snapshot_cid=$(echo "$snapshot_cid" | tr -d '[:space:]')

  if [[ -z "$snapshot_cid" || "$snapshot_cid" == "null" ]]; then
    error "Failed to store snapshot"
  fi

  success "Snapshot stored: $snapshot_cid"

  # Save metadata
  mkdir -p "$SNAPSHOTS_DIR"
  local metadata=$(jq -n \
    --arg cid "$snapshot_cid" \
    --argjson seq "$seq" \
    --arg ts "$timestamp" \
    --argjson count "$final_count" \
    '{cid: $cid, seq: $seq, ts: $ts, count: $count}')

  echo "$metadata" > "$SNAPSHOTS_DIR/snapshot-$seq.json"
  echo "$metadata" > "$SNAPSHOTS_DIR/latest.json"

  success "Snapshot metadata saved to $SNAPSHOTS_DIR"

  # Output summary
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo -e "${GREEN}Snapshot Build Complete${NC}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "CID:      $snapshot_cid"
  echo "Sequence: $seq"
  echo "Entities: $final_count"
  echo "Time:     $timestamp"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  # Output the CID for piping
  echo "$snapshot_cid"
}

# Main
main() {
  log "Starting snapshot builder..."

  # Check if docker container is running
  if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    error "Docker container '$CONTAINER_NAME' is not running"
  fi

  # Check if IPFS is accessible
  if ! curl -sf -X POST "$IPFS_API/version" > /dev/null; then
    error "Cannot connect to IPFS at $IPFS_API"
  fi

  build_snapshot
}

main "$@"
