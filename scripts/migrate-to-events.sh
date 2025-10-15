#!/bin/bash
# Migrate from old PI chain (v1) to event chain (v2)
# This is a ONE-TIME migration script

set -euo pipefail

# Load configuration from .env file (if exists)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [[ -f "$ENV_FILE" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    if [[ -z "${!key:-}" ]]; then
      export "$key=$value"
    fi
  done < <(grep -v '^#' "$ENV_FILE" | grep -v '^$')
fi

# Configuration
IPFS_API="${IPFS_API_URL:-http://localhost:5001/api/v0}"
INDEX_POINTER_PATH="${INDEX_POINTER_PATH:-/arke/index-pointer}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*" >&2; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Read index pointer from MFS
get_index_pointer() {
  local pointer=$(curl -sf -X POST "$IPFS_API/files/read?arg=$INDEX_POINTER_PATH" 2>/dev/null)
  if [[ -z "$pointer" ]]; then
    error "No index pointer found at $INDEX_POINTER_PATH"
  fi
  echo "$pointer"
}

# Walk old chain backwards and collect PIs in order
walk_old_chain() {
  local chain_head="$1"
  local pis=()
  local current="$chain_head"
  local count=0

  log "Walking old PI chain from head..."

  while [[ -n "$current" && "$current" != "null" ]]; do
    count=$((count + 1))

    # Fetch chain entry
    local entry=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$current" 2>/dev/null)

    if [[ -z "$entry" ]]; then
      warn "Failed to fetch chain entry $current, stopping"
      break
    fi

    # Extract PI
    local pi=$(echo "$entry" | jq -r '.pi')
    pis+=("$pi")

    # Move to previous
    local prev=$(echo "$entry" | jq -r '.prev["/"] // empty')
    if [[ -z "$prev" ]]; then
      break
    fi
    current="$prev"
  done

  success "Found $count PIs in old chain"

  # Reverse array (we walked backwards, need chronological order)
  local reversed=()
  for ((i=${#pis[@]}-1; i>=0; i--)); do
    reversed+=("${pis[$i]}")
  done

  # Return as JSON array
  printf '%s\n' "${reversed[@]}" | jq -R . | jq -s .
}

# Create event for a PI
create_event() {
  local pi="$1"
  local prev_event_cid="$2"
  local timestamp="$3"

  # Read current tip from MFS
  local shard1="${pi:0:2}"
  local shard2="${pi:2:2}"
  local tip_path="/arke/index/$shard1/$shard2/${pi}.tip"

  local tip_cid=$(curl -sf -X POST "$IPFS_API/files/read?arg=$tip_path" 2>/dev/null | tr -d '\n')

  if [[ -z "$tip_cid" ]]; then
    warn "Failed to read tip for $pi, skipping"
    return 1
  fi

  # Fetch manifest to get version
  local manifest=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$tip_cid" 2>/dev/null)
  local ver=$(echo "$manifest" | jq -r '.ver // 1')

  # Build prev link
  local prev_link="null"
  if [[ -n "$prev_event_cid" && "$prev_event_cid" != "null" ]]; then
    prev_link=$(jq -n --arg cid "$prev_event_cid" '{"/": $cid}')
  fi

  # Create event object
  local event=$(jq -n \
    --arg schema "arke/event@v1" \
    --arg type "create" \
    --arg pi "$pi" \
    --argjson ver "$ver" \
    --arg tip_cid "$tip_cid" \
    --arg ts "$timestamp" \
    --argjson prev "$prev_link" \
    '{
      schema: $schema,
      type: $type,
      pi: $pi,
      ver: $ver,
      tip_cid: {"/": $tip_cid},
      ts: $ts,
      prev: $prev
    }')

  # Store event as DAG-JSON
  local event_cid=$(echo "$event" | curl -sf -X POST \
    -F "file=@-" \
    "$IPFS_API/dag/put?store-codec=dag-json&input-codec=json&pin=true" | \
    jq -r '.Cid["/"]' 2>&1 | tr -d '[:space:]')

  if [[ -z "$event_cid" ]]; then
    error "Failed to store event for $pi"
  fi

  echo "$event_cid"
}

# Main migration logic
migrate() {
  log "Starting migration from PI chain (v1) to event chain (v2)..."

  # Read current index pointer
  log "Reading index pointer..."
  local pointer=$(get_index_pointer)

  local schema=$(echo "$pointer" | jq -r '.schema // "arke/index-pointer@v1"')

  if [[ "$schema" == "arke/index-pointer@v2" ]]; then
    warn "Already migrated to v2! Exiting."
    exit 0
  fi

  local old_chain_head=$(echo "$pointer" | jq -r '.recent_chain_head // empty')

  if [[ -z "$old_chain_head" || "$old_chain_head" == "null" ]]; then
    warn "No old chain found (recent_chain_head is empty)"
    log "Creating empty v2 index pointer..."

    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local new_pointer=$(jq -n \
      --arg schema "arke/index-pointer@v2" \
      --arg ts "$timestamp" \
      '{
        schema: $schema,
        event_head: null,
        event_count: 0,
        latest_snapshot_cid: null,
        snapshot_event_cid: null,
        snapshot_seq: 0,
        snapshot_count: 0,
        snapshot_ts: null,
        total_count: 0,
        last_updated: $ts
      }')

    echo "$new_pointer" | curl -sf -X POST \
      -F "file=@-" \
      "$IPFS_API/files/write?arg=$INDEX_POINTER_PATH&create=true&truncate=true&parents=true" >/dev/null

    success "Migration complete (no data to migrate)"
    exit 0
  fi

  log "Old chain head: $old_chain_head"

  # Walk old chain to get PIs in chronological order
  local pis_json=$(walk_old_chain "$old_chain_head")
  local pi_count=$(echo "$pis_json" | jq 'length')

  log "Migrating $pi_count PIs to event chain..."

  # Create events in chronological order
  local prev_event_cid="null"
  local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  local count=0

  while IFS= read -r pi; do
    count=$((count + 1))
    log "  [$count/$pi_count] Creating event for $pi..."

    local event_cid=$(create_event "$pi" "$prev_event_cid" "$timestamp")

    if [[ -n "$event_cid" ]]; then
      prev_event_cid="$event_cid"
    else
      warn "Failed to create event for $pi, continuing..."
    fi
  done < <(echo "$pis_json" | jq -r '.[]')

  if [[ "$prev_event_cid" == "null" ]]; then
    error "Failed to create any events"
  fi

  local event_head="$prev_event_cid"
  local event_count=$pi_count

  success "Created $event_count events, head: $event_head"

  # Update index pointer to v2
  log "Updating index pointer to v2..."

  # Preserve snapshot fields from old pointer
  local latest_snapshot=$(echo "$pointer" | jq -r '.latest_snapshot_cid // null')
  local snapshot_seq=$(echo "$pointer" | jq -r '.snapshot_seq // 0')
  local snapshot_count=$(echo "$pointer" | jq -r '.snapshot_count // 0')
  local snapshot_ts=$(echo "$pointer" | jq -r '.snapshot_ts // null')
  local total_count=$(echo "$pointer" | jq -r '.total_count // 0')

  # Build snapshot fields with proper null handling
  local latest_snapshot_value="null"
  if [[ -n "$latest_snapshot" && "$latest_snapshot" != "null" ]]; then
    latest_snapshot_value="\"$latest_snapshot\""
  fi

  local snapshot_ts_value="null"
  if [[ -n "$snapshot_ts" && "$snapshot_ts" != "null" ]]; then
    snapshot_ts_value="\"$snapshot_ts\""
  fi

  local new_pointer=$(jq -n \
    --arg schema "arke/index-pointer@v2" \
    --arg event_head "$event_head" \
    --argjson event_count "$event_count" \
    --argjson latest_snapshot "$latest_snapshot_value" \
    --arg snapshot_event_cid "$event_head" \
    --argjson snapshot_seq "$snapshot_seq" \
    --argjson snapshot_count "$snapshot_count" \
    --argjson snapshot_ts "$snapshot_ts_value" \
    --argjson total_count "$total_count" \
    --arg updated "$timestamp" \
    '{
      schema: $schema,
      event_head: $event_head,
      event_count: $event_count,
      latest_snapshot_cid: $latest_snapshot,
      snapshot_event_cid: $snapshot_event_cid,
      snapshot_seq: $snapshot_seq,
      snapshot_count: $snapshot_count,
      snapshot_ts: $snapshot_ts,
      total_count: $total_count,
      last_updated: $updated
    }')

  # Write new pointer
  echo "$new_pointer" | curl -sf -X POST \
    -F "file=@-" \
    "$IPFS_API/files/write?arg=$INDEX_POINTER_PATH&create=true&truncate=true&parents=true" >/dev/null

  success "Index pointer updated to v2"

  # Summary
  echo "" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo -e "${GREEN}Migration Complete${NC}" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "Event head:    $event_head" >&2
  echo "Event count:   $event_count" >&2
  echo "Total PIs:     $total_count" >&2
  echo "Schema:        arke/index-pointer@v2" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "" >&2
}

# Main
main() {
  # Check IPFS availability
  if ! curl -sf -X POST "$IPFS_API/version" > /dev/null; then
    error "Cannot connect to IPFS at $IPFS_API"
  fi

  migrate
}

main "$@"
