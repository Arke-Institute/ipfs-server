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

# Walk event chain and collect unique PIs, reading current tips from MFS
walk_event_chain() {
  local event_head="$1"
  local entries="[]"
  local current="$event_head"
  local count=0
  declare -A seen_pis  # Track unique PIs

  log "Walking event chain from head (collecting unique PIs)..."

  while [[ -n "$current" && "$current" != "null" ]]; do
    # Fetch event entry
    local event=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$current" 2>/dev/null)

    if [[ -z "$event" ]]; then
      warn "Failed to fetch event $current, stopping"
      break
    fi

    # Extract PI and timestamp from event
    local pi=$(echo "$event" | jq -r '.pi')
    local ts=$(echo "$event" | jq -r '.ts')
    local event_type=$(echo "$event" | jq -r '.type')

    # Skip if we've already seen this PI
    if [[ -n "${seen_pis[$pi]:-}" ]]; then
      # Move to previous event
      local prev=$(echo "$event" | jq -r '.prev["/"] // empty')
      if [[ -z "$prev" ]]; then
        break
      fi
      current="$prev"
      continue
    fi

    # Mark as seen
    seen_pis[$pi]=1
    count=$((count + 1))

    # Read CURRENT tip from MFS (always up-to-date)
    local shard1="${pi:0:2}"
    local shard2="${pi:2:2}"
    local tip_path="/arke/index/$shard1/$shard2/${pi}.tip"

    local tip_cid=$(curl -sf -X POST "$IPFS_API/files/read?arg=$tip_path" 2>/dev/null | tr -d '\n')

    if [[ -z "$tip_cid" ]]; then
      warn "Failed to read tip for $pi, skipping"
      current=$(echo "$event" | jq -r '.prev["/"] // empty')
      continue
    fi

    # Fetch manifest to get current version number
    local manifest=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$tip_cid" 2>/dev/null)
    local ver=$(echo "$manifest" | jq -r '.ver // 0')

    log "  [$count] $pi (v$ver, tip from MFS)"

    # Build snapshot entry
    # IMPORTANT:
    # - tip_cid as IPLD LINK so CAR exporter includes manifests + version history
    # - chain_cid as IPLD LINK so CAR exporter includes event entries
    # - Both needed for complete DR restore
    local new_entry=$(jq -n \
      --arg pi "$pi" \
      --argjson ver "$ver" \
      --arg tip_cid "$tip_cid" \
      --arg ts "$ts" \
      --arg chain_cid "$current" \
      '{pi: $pi, ver: $ver, tip_cid: {"/": $tip_cid}, ts: $ts, chain_cid: {"/": $chain_cid}}')

    entries=$(echo "$entries" | jq --argjson entry "$new_entry" '. = [$entry] + .')

    # Move to previous event
    local prev=$(echo "$event" | jq -r '.prev["/"] // empty')
    if [[ -z "$prev" ]]; then
      break
    fi
    current="$prev"
  done

  success "Collected $count unique PIs from event chain (tips read from MFS)"
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
  local event_head=$(echo "$pointer" | jq -r '.event_head // empty')

  local new_seq=$((prev_seq + 1))

  log "Previous snapshot: ${prev_snapshot:-none}"
  log "Event head: ${event_head:-none}"
  log "New sequence: $new_seq"

  # Walk entire event chain (reads current tips from MFS for each unique PI)
  # No merging with old snapshots - full walk ensures all tips are current
  local all_entries="[]"
  if [[ -n "$event_head" && "$event_head" != "null" ]]; then
    all_entries=$(walk_event_chain "$event_head")
  fi

  local total_count=$(echo "$all_entries" | jq 'length')

  success "Total entries to snapshot: $total_count"

  if [[ $total_count -eq 0 ]]; then
    error "No entries to snapshot"
  fi

  # Create snapshot object with entries array (no chunking)
  # IMPORTANT: Use stdin for entries to avoid "Argument list too long" error with 1000+ entities
  local prev_link="null"
  if [[ -n "$prev_snapshot" && "$prev_snapshot" != "null" ]]; then
    prev_link=$(jq -n --arg cid "$prev_snapshot" '{"/": $cid}')
  fi

  # Include event_cid checkpoint (for mirrors to start from)
  local snapshot=$(echo "$all_entries" | jq \
    --arg schema "arke/snapshot@v1" \
    --argjson seq "$new_seq" \
    --arg ts "$timestamp" \
    --argjson prev "$prev_link" \
    --arg event_cid "$event_head" \
    --argjson total "$total_count" \
    '{
      schema: $schema,
      seq: $seq,
      ts: $ts,
      prev_snapshot: $prev,
      event_cid: $event_cid,
      total_count: $total,
      entries: .
    }')

  log "Storing snapshot metadata..."
  # Use HTTP API instead of docker exec (for container compatibility)
  local snapshot_cid=$(echo "$snapshot" | curl -sf -X POST \
    -F "file=@-" \
    "$IPFS_API/dag/put?store-codec=dag-json&input-codec=json&pin=true" | \
    jq -r '.Cid["/"]' 2>&1 | tr -d '[:space:]')

  success "Snapshot created: $snapshot_cid"

  # Update index pointer
  # IMPORTANT: Keep event_head pointing to the latest event!
  # This maintains chain continuity - new events link to it via prev.
  # Store snapshot_event_cid as checkpoint for mirrors.
  log "Updating index pointer (preserving event head)..."

  # Build event_head value (null if empty, otherwise the CID)
  local event_head_value="null"
  if [[ -n "$event_head" && "$event_head" != "null" ]]; then
    event_head_value="\"$event_head\""
  fi

  # Get current event_count from pointer (preserve it)
  local event_count=$(echo "$pointer" | jq -r '.event_count // 0')

  local new_pointer=$(jq -n \
    --arg schema "arke/index-pointer@v2" \
    --arg snapshot_cid "$snapshot_cid" \
    --arg snapshot_event_cid "$event_head" \
    --argjson seq "$new_seq" \
    --argjson count "$total_count" \
    --arg ts "$timestamp" \
    --arg updated "$timestamp" \
    --argjson event_head "$event_head_value" \
    --argjson event_count "$event_count" \
    '{
      schema: $schema,
      event_head: $event_head,
      event_count: $event_count,
      latest_snapshot_cid: $snapshot_cid,
      snapshot_event_cid: $snapshot_event_cid,
      snapshot_seq: $seq,
      snapshot_count: $count,
      snapshot_ts: $ts,
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
