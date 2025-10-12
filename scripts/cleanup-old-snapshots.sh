#!/bin/bash
# Cleanup old snapshots - keep only last N snapshots pinned
# Unpins old snapshots and runs garbage collection to reclaim disk space

set -euo pipefail

KEEP_LAST="${KEEP_LAST:-5}"
CONTAINER_NAME="${CONTAINER_NAME:-ipfs-node}"
SNAPSHOTS_DIR="${SNAPSHOTS_DIR:-./snapshots}"
IPFS_API="${IPFS_API:-http://localhost:5001/api/v0}"
INDEX_POINTER_PATH="/arke/index-pointer"
DRY_RUN="${DRY_RUN:-false}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*" >&2; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Get current snapshot CID (never unpin this)
get_current_snapshot_cid() {
  curl -sf -X POST "$IPFS_API/files/read?arg=$INDEX_POINTER_PATH" 2>/dev/null | jq -r '.latest_snapshot_cid // empty'
}

# Main cleanup logic
cleanup_old_snapshots() {
  log "Starting snapshot pin cleanup (keep last $KEEP_LAST snapshots)..."

  # Check if snapshots directory exists
  if [[ ! -d "$SNAPSHOTS_DIR" ]]; then
    warn "Snapshots directory not found: $SNAPSHOTS_DIR"
    return 0
  fi

  # Get current snapshot CID
  local current_snapshot=$(get_current_snapshot_cid)
  if [[ -n "$current_snapshot" ]]; then
    log "Current snapshot (will not unpin): $current_snapshot"
  else
    warn "No current snapshot found in index pointer"
  fi

  # Get list of all snapshot metadata files, sorted by sequence number
  local all_snapshots=$(ls -1 "$SNAPSHOTS_DIR"/snapshot-*.json 2>/dev/null | sort -t- -k2 -n)

  if [[ -z "$all_snapshots" ]]; then
    log "No snapshot metadata files found"
    return 0
  fi

  local total_count=$(echo "$all_snapshots" | wc -l | tr -d ' ')
  log "Found $total_count snapshot metadata files"

  # Determine which snapshots to keep
  local keep_count=$KEEP_LAST
  if [[ $total_count -le $keep_count ]]; then
    log "Only $total_count snapshots exist, keeping all (threshold: $keep_count)"
    return 0
  fi

  # Get snapshots to delete (oldest first, keep last N)
  local delete_count=$((total_count - keep_count))
  local old_snapshots=$(echo "$all_snapshots" | head -n $delete_count)

  log "Will unpin and remove $delete_count old snapshots"

  # Unpin old snapshots
  local unpinned=0
  local skipped=0
  local failed=0

  while IFS= read -r snapshot_file; do
    local snapshot_cid=$(jq -r '.cid' "$snapshot_file" 2>/dev/null)
    local snapshot_seq=$(jq -r '.seq' "$snapshot_file" 2>/dev/null)

    if [[ -z "$snapshot_cid" || "$snapshot_cid" == "null" ]]; then
      warn "Could not read CID from $snapshot_file, skipping"
      continue
    fi

    # Safety check: never unpin current snapshot
    if [[ "$snapshot_cid" == "$current_snapshot" ]]; then
      warn "Snapshot $snapshot_cid (seq $snapshot_seq) is current, skipping"
      ((skipped++))
      continue
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
      log "[DRY RUN] Would unpin snapshot $snapshot_cid (seq $snapshot_seq)"
    else
      log "Unpinning snapshot $snapshot_cid (seq $snapshot_seq)..."

      if docker exec "$CONTAINER_NAME" ipfs pin rm "$snapshot_cid" 2>/dev/null; then
        ((unpinned++))

        # Remove metadata file
        rm -f "$snapshot_file"
        success "Unpinned and removed snapshot $snapshot_seq"
      else
        warn "Failed to unpin $snapshot_cid (may already be unpinned)"
        ((failed++))

        # Still remove metadata file even if unpin failed
        rm -f "$snapshot_file"
      fi
    fi
  done <<< "$old_snapshots"

  success "Cleanup complete: $unpinned unpinned, $skipped skipped, $failed failed"

  # Run garbage collection to reclaim disk space
  if [[ "$DRY_RUN" == "false" && $unpinned -gt 0 ]]; then
    log "Running garbage collection to reclaim disk space..."

    if docker exec "$CONTAINER_NAME" ipfs repo gc 2>&1 | head -5; then
      success "Garbage collection complete"
    else
      warn "Garbage collection failed (non-fatal)"
    fi
  fi

  # Show final repo stats
  if [[ "$DRY_RUN" == "false" ]]; then
    log "Repo stats after cleanup:"
    docker exec "$CONTAINER_NAME" ipfs repo stat --human 2>/dev/null | grep -E "RepoSize|NumObjects" || true
  fi
}

# Main
main() {
  if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    error "Docker container '$CONTAINER_NAME' is not running"
    exit 1
  fi

  if ! curl -sf -X POST "$IPFS_API/version" > /dev/null; then
    error "Cannot connect to IPFS at $IPFS_API"
    exit 1
  fi

  cleanup_old_snapshots

  echo "" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo -e "${GREEN}Snapshot Cleanup Complete${NC}" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "Kept:    Last $KEEP_LAST snapshots" >&2
  echo "Mode:    ${DRY_RUN}" >&2
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
  echo "" >&2
}

main "$@"
