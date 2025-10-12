#!/bin/bash
# Build snapshot from PI chain with current tips from MFS
# Walks chain, reads current tips from MFS, stores as direct array

set -euo pipefail

# Lock file to prevent concurrent builds
LOCK_FILE="/tmp/arke-snapshot.lock"

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

# Walk entire chain and collect all PIs, reading current tips from MFS
walk_chain() {
  local chain_head="$1"
  local entries="[]"
  local current="$chain_head"
  local count=0

  log "Walking entire PI chain from head (reading current tips from MFS)..."

  while [[ -n "$current" && "$current" != "null" ]]; do
    count=$((count + 1))

    # Fetch chain entry (just PI + timestamp + prev)
    local entry=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$current" 2>/dev/null)

    if [[ -z "$entry" ]]; then
      warn "Failed to fetch chain entry $current, stopping"
      break
    fi

    # Extract PI and timestamp from chain entry
    local pi=$(echo "$entry" | jq -r '.pi')
    local ts=$(echo "$entry" | jq -r '.ts')

    # Read CURRENT tip from MFS (not from chain entry)
    local shard1="${pi:0:2}"
    local shard2="${pi:2:2}"
    local tip_path="/arke/index/$shard1/$shard2/${pi}.tip"

    local tip_cid=$(curl -sf -X POST "$IPFS_API/files/read?arg=$tip_path" 2>/dev/null | tr -d '\n')

    if [[ -z "$tip_cid" ]]; then
      warn "Failed to read tip for $pi, skipping"
      current=$(echo "$entry" | jq -r '.prev["/"] // empty')
      continue
    fi

    # Fetch manifest to get current version number
    local manifest=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$tip_cid" 2>/dev/null)
    local ver=$(echo "$manifest" | jq -r '.ver // 0')

    log "  [$count] $pi (v$ver, tip from MFS)"

    # Build snapshot entry
    # IMPORTANT:
    # - tip_cid as IPLD LINK so CAR exporter includes manifests + version history
    # - chain_cid as IPLD LINK so CAR exporter includes chain entries
    # - Both needed for complete DR restore
    local new_entry=$(jq -n \
      --arg pi "$pi" \
      --argjson ver "$ver" \
      --arg tip_cid "$tip_cid" \
      --arg ts "$ts" \
      --arg chain_cid "$current" \
      '{pi: $pi, ver: $ver, tip_cid: {"/": $tip_cid}, ts: $ts, chain_cid: {"/": $chain_cid}}')

    entries=$(echo "$entries" | jq --argjson entry "$new_entry" '. = [$entry] + .')

    # Move to previous
    local prev=$(echo "$entry" | jq -r '.prev["/"] // empty')
    if [[ -z "$prev" ]]; then
      break
    fi
    current="$prev"
  done

  success "Collected $count entries from chain (tips read from MFS)"
  echo "$entries"
}

# No chunking - entries stored directly in snapshot

# Main snapshot build logic
build_snapshot() {
  local start_time=$(date +%s)
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

  # Walk entire chain (reads current tips from MFS for each PI)
  # No merging with old snapshots - full walk ensures all tips are current
  local all_entries="[]"
  if [[ -n "$chain_head" && "$chain_head" != "null" ]]; then
    all_entries=$(walk_chain "$chain_head")
  fi

  local total_count=$(echo "$all_entries" | jq 'length')

  success "Total entries to snapshot: $total_count"

  if [[ $total_count -eq 0 ]]; then
    error "No entries to snapshot"
  fi

  # Create snapshot object with entries array (no chunking)
  local prev_link="null"
  if [[ -n "$prev_snapshot" && "$prev_snapshot" != "null" ]]; then
    prev_link=$(jq -n --arg cid "$prev_snapshot" '{"/": $cid}')
  fi

  local snapshot=$(jq -n \
    --arg schema "arke/snapshot@v0" \
    --argjson seq "$new_seq" \
    --arg ts "$timestamp" \
    --argjson prev "$prev_link" \
    --argjson total "$total_count" \
    --argjson entries "$all_entries" \
    '{
      schema: $schema,
      seq: $seq,
      ts: $ts,
      prev_snapshot: $prev,
      total_count: $total,
      entries: $entries
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

  # Calculate duration
  local end_time=$(date +%s)
  local duration=$((end_time - start_time))

  # Summary
  echo "" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo -e "${GREEN}Snapshot Build Complete${NC}" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "CID:      $snapshot_cid" >&2
  echo "Sequence: $new_seq" >&2
  echo "Entities: $total_count" >&2
  echo "Time:     $timestamp" >&2
  echo "Duration: ${duration}s" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "" >&2

  echo "$snapshot_cid"
}

# Main
main() {
  # Check for existing lock file
  if [[ -f "$LOCK_FILE" ]]; then
    local lock_age=$(($(date +%s) - $(stat -f %m "$LOCK_FILE" 2>/dev/null || stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)))

    # If lock is older than 10 minutes, it's probably stale (crashed build)
    if [[ $lock_age -lt 600 ]]; then
      error "Snapshot build already in progress (lock file exists: $LOCK_FILE)"
    else
      warn "Removing stale lock file (age: ${lock_age}s)"
      rm -f "$LOCK_FILE"
    fi
  fi

  # Create lock file with PID and timestamp
  echo "$$|$(date +%s)" > "$LOCK_FILE"

  # Ensure lock is removed on exit (success or failure)
  trap "rm -f $LOCK_FILE" EXIT

  # Check IPFS availability (skip docker check if running inside container)
  if ! curl -sf -X POST "$IPFS_API/version" > /dev/null; then
    error "Cannot connect to IPFS at $IPFS_API"
  fi

  build_snapshot
}

main "$@"
