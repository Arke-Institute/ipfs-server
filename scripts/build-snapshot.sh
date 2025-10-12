#!/bin/bash
# Build chunked snapshot from recent chain + previous snapshot
# NO MFS TRAVERSAL - uses DAG operations only

set -euo pipefail

# Load configuration from .env file (if exists), but don't override existing env vars
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [[ -f "$ENV_FILE" ]]; then
  # Source .env file, but only set variables that aren't already set
  while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    # Only set if not already set
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < <(grep -v '^#' "$ENV_FILE" | grep -v '^$')
fi

# Configuration (env vars take precedence over .env, then defaults)
IPFS_API="${IPFS_API_URL:-http://localhost:5001/api/v0}"
CONTAINER_NAME="${CONTAINER_NAME:-ipfs-node}"
CHUNK_SIZE="${CHUNK_SIZE:-10000}"
SNAPSHOTS_DIR="${SNAPSHOTS_DIR:-./snapshots}"
INDEX_POINTER_PATH="${INDEX_POINTER_PATH:-/arke/index-pointer}"
AUTO_CLEANUP="${AUTO_CLEANUP:-true}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*" >&2; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# Read index pointer from MFS
get_index_pointer() {
  local pointer=$(curl -sf -X POST "$IPFS_API/files/read?arg=$INDEX_POINTER_PATH" 2>/dev/null)
  if [[ -z "$pointer" ]]; then
    echo '{}'
  else
    echo "$pointer"
  fi
}

# Walk recent chain and collect all entries
walk_chain() {
  local chain_head="$1"
  local entries="[]"
  local current="$chain_head"
  local count=0

  log "Walking recent chain from $chain_head..."

  while [[ -n "$current" && "$current" != "null" ]]; do
    count=$((count + 1))

    # Fetch chain entry
    local entry=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$current" 2>/dev/null)

    if [[ -z "$entry" ]]; then
      warn "Failed to fetch chain entry $current, stopping"
      break
    fi

    # Extract data
    local pi=$(echo "$entry" | jq -r '.pi')
    local ver=$(echo "$entry" | jq -r '.ver')
    local tip=$(echo "$entry" | jq -r '.tip["/"]')
    local ts=$(echo "$entry" | jq -r '.ts')

    log "  [$count] $pi (ver $ver)"

    # Add to entries array
    # IMPORTANT: Store the chain CID as an IPLD link so CAR export includes it!
    local new_entry=$(jq -n \
      --arg pi "$pi" \
      --argjson ver "$ver" \
      --arg tip "$tip" \
      --arg ts "$ts" \
      --arg chain_cid "$current" \
      '{pi: $pi, ver: $ver, tip: {"/": $tip}, ts: $ts, chain_cid: {"/": $chain_cid}}')

    entries=$(echo "$entries" | jq --argjson entry "$new_entry" '. = [$entry] + .')

    # Move to previous
    local prev=$(echo "$entry" | jq -r '.prev["/"] // empty')
    if [[ -z "$prev" ]]; then
      break
    fi
    current="$prev"
  done

  success "Collected $count entries from chain"
  echo "$entries"
}

# Read previous snapshot entries
get_snapshot_entries() {
  local snapshot_cid="$1"

  if [[ -z "$snapshot_cid" || "$snapshot_cid" == "null" ]]; then
    echo "[]"
    return
  fi

  log "Reading previous snapshot $snapshot_cid..."

  # Fetch snapshot metadata
  local snapshot=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$snapshot_cid" 2>/dev/null)

  if [[ -z "$snapshot" ]]; then
    warn "Failed to fetch snapshot, starting fresh"
    echo "[]"
    return
  fi

  local all_entries="[]"
  local chunk_count=$(echo "$snapshot" | jq -r '.chunks | length')

  log "Snapshot has $chunk_count chunks"

  # Fetch all chunks
  for i in $(seq 0 $((chunk_count - 1))); do
    local chunk_cid=$(echo "$snapshot" | jq -r ".chunks[$i][\"/\"]")
    log "  Fetching chunk $i: $chunk_cid"

    local chunk=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$chunk_cid" 2>/dev/null)
    local chunk_entries=$(echo "$chunk" | jq '.entries')

    all_entries=$(echo "$all_entries" | jq --argjson chunk "$chunk_entries" '. + $chunk')
  done

  local total=$(echo "$all_entries" | jq 'length')
  success "Loaded $total entries from previous snapshot"

  echo "$all_entries"
}

# Create chunks as a linked list (chain-style)
# Returns the CID of the HEAD chunk (most recent)
create_chunks() {
  local entries="$1"
  local total=$(echo "$entries" | jq 'length')
  local prev_chunk_cid=""
  local chunk_count=0

  log "Creating chunk linked list (size=$CHUNK_SIZE) from $total entries..."

  # Build chunks from OLDEST to NEWEST (so we can link backwards)
  local offset=$((total - total % CHUNK_SIZE))
  if [[ $offset -eq $total && $total -gt 0 ]]; then
    offset=$((offset - CHUNK_SIZE))
  fi

  # Process chunks in reverse order (oldest first)
  local chunk_cids=()
  local temp_offset=0
  while [[ $temp_offset -lt $total ]]; do
    local chunk_entries=$(echo "$entries" | jq --argjson offset "$temp_offset" --argjson size "$CHUNK_SIZE" \
      '.[$offset:($offset + $size)]')

    local chunk_idx=$((temp_offset / CHUNK_SIZE))

    # Create chunk object with prev link
    local prev_link="null"
    if [[ -n "$prev_chunk_cid" ]]; then
      prev_link=$(jq -n --arg cid "$prev_chunk_cid" '{"/": $cid}')
    fi

    local chunk_obj=$(jq -n \
      --arg schema "arke/snapshot-chunk@v2" \
      --argjson chunk_index "$chunk_idx" \
      --argjson entries "$chunk_entries" \
      --argjson prev "$prev_link" \
      '{schema: $schema, chunk_index: $chunk_index, entries: $entries, prev: $prev}')

    # Store chunk as DAG-JSON
    # Use HTTP API instead of docker exec (for container compatibility)
    local chunk_cid=$(echo "$chunk_obj" | curl -sf -X POST \
      -F "file=@-" \
      "$IPFS_API/dag/put?store-codec=dag-json&input-codec=json&pin=true" | \
      jq -r '.Cid["/"]' 2>&1 | tr -d '[:space:]')

    log "  Chunk $chunk_idx: $chunk_cid ($(echo "$chunk_entries" | jq 'length') entries)"

    prev_chunk_cid="$chunk_cid"
    chunk_count=$((chunk_count + 1))
    temp_offset=$((temp_offset + CHUNK_SIZE))
  done

  success "Created $chunk_count chunks in linked list"

  # Return the HEAD (last chunk created, which is the newest)
  echo "$prev_chunk_cid"
}

# Main snapshot build logic
build_snapshot() {
  local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  log "Reading index pointer..."
  local pointer=$(get_index_pointer)

  local prev_snapshot=$(echo "$pointer" | jq -r '.latest_snapshot_cid // empty')
  local prev_seq=$(echo "$pointer" | jq -r '.snapshot_seq // 0')
  local chain_head=$(echo "$pointer" | jq -r '.recent_chain_head // empty')

  local new_seq=$((prev_seq + 1))

  log "Previous snapshot: ${prev_snapshot:-none}"
  log "Chain head: ${chain_head:-none}"
  log "New sequence: $new_seq"

  # Collect all entries
  local chain_entries="[]"
  if [[ -n "$chain_head" && "$chain_head" != "null" ]]; then
    chain_entries=$(walk_chain "$chain_head")
  fi

  local snapshot_entries="[]"
  if [[ -n "$prev_snapshot" && "$prev_snapshot" != "null" ]]; then
    snapshot_entries=$(get_snapshot_entries "$prev_snapshot")
  fi

  # Merge: chain entries (newest) + snapshot entries (oldest)
  log "Merging entries..."
  local all_entries=$(echo "$chain_entries" "$snapshot_entries" | jq -s '.[0] + .[1]')
  local total_count=$(echo "$all_entries" | jq 'length')

  success "Total entries to snapshot: $total_count"

  if [[ $total_count -eq 0 ]]; then
    error "No entries to snapshot"
  fi

  # Create chunk linked list (returns head CID)
  local chunks_head_cid=$(create_chunks "$all_entries")

  # Create snapshot object with entries_head
  local prev_link="null"
  if [[ -n "$prev_snapshot" && "$prev_snapshot" != "null" ]]; then
    prev_link=$(jq -n --arg cid "$prev_snapshot" '{"/": $cid}')
  fi

  local entries_head_link="null"
  if [[ -n "$chunks_head_cid" && "$chunks_head_cid" != "null" ]]; then
    entries_head_link=$(jq -n --arg cid "$chunks_head_cid" '{"/": $cid}')
  fi

  local snapshot=$(jq -n \
    --arg schema "arke/snapshot@v3" \
    --argjson seq "$new_seq" \
    --arg ts "$timestamp" \
    --argjson prev "$prev_link" \
    --argjson total "$total_count" \
    --argjson chunk_size "$CHUNK_SIZE" \
    --argjson entries_head "$entries_head_link" \
    '{
      schema: $schema,
      seq: $seq,
      ts: $ts,
      prev_snapshot: $prev,
      total_count: $total,
      chunk_size: $chunk_size,
      entries_head: $entries_head
    }')

  log "Storing snapshot metadata..."
  # Use HTTP API instead of docker exec (for container compatibility)
  local snapshot_cid=$(echo "$snapshot" | curl -sf -X POST \
    -F "file=@-" \
    "$IPFS_API/dag/put?store-codec=dag-json&input-codec=json&pin=true" | \
    jq -r '.Cid["/"]' 2>&1 | tr -d '[:space:]')

  success "Snapshot created: $snapshot_cid"

  # Update index pointer
  # IMPORTANT: Keep recent_chain_head pointing to the latest entity!
  # This maintains chain continuity - new entities link to it via prev.
  # Only recent_count resets to 0 (meaning: no new entities since snapshot).
  log "Updating index pointer (preserving chain head)..."

  # Build chain_head value (null if empty, otherwise the CID)
  local chain_head_value="null"
  if [[ -n "$chain_head" && "$chain_head" != "null" ]]; then
    chain_head_value="\"$chain_head\""
  fi

  local new_pointer=$(jq -n \
    --arg schema "arke/index-pointer@v1" \
    --arg snapshot_cid "$snapshot_cid" \
    --argjson seq "$new_seq" \
    --argjson count "$total_count" \
    --arg ts "$timestamp" \
    --arg updated "$timestamp" \
    --argjson chain_head "$chain_head_value" \
    '{
      schema: $schema,
      latest_snapshot_cid: $snapshot_cid,
      snapshot_seq: $seq,
      snapshot_count: $count,
      snapshot_ts: $ts,
      recent_chain_head: $chain_head,
      recent_count: 0,
      total_count: $count,
      last_updated: $updated
    }')

  # Write to MFS
  echo "$new_pointer" | curl -sf -X POST \
    -F "file=@-" \
    "$IPFS_API/files/write?arg=$INDEX_POINTER_PATH&create=true&truncate=true&parents=true" >/dev/null

  # Save metadata
  mkdir -p "$SNAPSHOTS_DIR"
  local metadata=$(jq -n \
    --arg cid "$snapshot_cid" \
    --argjson seq "$new_seq" \
    --arg ts "$timestamp" \
    --argjson count "$total_count" \
    '{cid: $cid, seq: $seq, ts: $ts, count: $count}')

  echo "$metadata" > "$SNAPSHOTS_DIR/snapshot-$new_seq.json"
  echo "$metadata" > "$SNAPSHOTS_DIR/latest.json"

  success "Snapshot metadata saved"

  # Run automatic pin cleanup
  if [[ "$AUTO_CLEANUP" == "true" ]]; then
    log "Running automatic pin cleanup..."
    local script_dir=$(dirname "$0")
    if [[ -x "$script_dir/cleanup-old-snapshots.sh" ]]; then
      "$script_dir/cleanup-old-snapshots.sh" || warn "Pin cleanup failed (non-fatal)"
    else
      warn "Cleanup script not found or not executable: $script_dir/cleanup-old-snapshots.sh"
    fi
  fi

  # Summary
  echo "" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo -e "${GREEN}Snapshot Build Complete${NC}" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "CID:      $snapshot_cid" >&2
  echo "Sequence: $new_seq" >&2
  echo "Entities: $total_count" >&2
  echo "Time:     $timestamp" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "" >&2

  echo "$snapshot_cid"
}

# Main
main() {
  # Check IPFS availability (skip docker check if running inside container)
  if ! curl -sf -X POST "$IPFS_API/version" > /dev/null; then
    error "Cannot connect to IPFS at $IPFS_API"
  fi

  build_snapshot
}

main "$@"
