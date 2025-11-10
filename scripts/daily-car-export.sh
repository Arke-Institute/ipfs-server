#!/bin/bash
# Daily CAR Export Script for Arke IPFS Server
#
# This script exports the latest snapshot to a CAR (Content Addressable aRchive)
# file and uploads it to S3 for disaster recovery purposes.
#
# HOW IT WORKS:
# 1. Reads the latest snapshot CID from snapshots/latest.json
# 2. Calls the Python DR module (dr.export_car) inside the ipfs-api container
# 3. The Python module:
#    - Walks the snapshot DAG and collects all CIDs (manifests, components, events)
#    - Exports them to a CAR file in /app/backups/
#    - Uploads CAR + metadata to S3
#
# DEPLOYMENT:
# This script should be added to crontab on the EC2 instance:
#   0 2 * * * /home/ubuntu/ipfs-server/scripts/daily-car-export.sh >> /var/log/arke-backup.log 2>&1
#
# REQUIREMENTS:
# - Docker container 'ipfs-api' must be running
# - Snapshots are being built hourly (automated via API service)
# - IAM role with S3 write permissions attached to EC2 instance
# - jq command-line JSON processor installed
#
# LOGS:
# - Output: /var/log/arke-backup.log
# - Snapshot build logs: /app/logs/snapshot-build.log (in container)
#
# S3 STRUCTURE:
# s3://arke-ipfs-backups-{account-id}/backups/{instance-id}/
#   └── arke-{seq}-{timestamp}.car
#   └── arke-{seq}-{timestamp}.json (metadata)
#

set -e

LOG_FILE="/var/log/arke-backup.log"
SNAPSHOT_DIR="/home/ubuntu/ipfs-server/snapshots"
SNAPSHOT_FILE="$SNAPSHOT_DIR/latest.json"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# Logging functions
log() {
    echo -e "${BLUE}[$(date -u +"%Y-%m-%d %H:%M:%S UTC")]${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] ERROR:${NC} $1" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] SUCCESS:${NC} $1" | tee -a "$LOG_FILE"
}

# Start backup process
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "Starting daily CAR backup process (Python DR module)"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check Docker is running
if ! docker ps > /dev/null 2>&1; then
    log_error "Docker is not running"
    exit 1
fi

# Check ipfs-api container is running
if ! docker ps --format '{{.Names}}' | grep -q '^ipfs-api$'; then
    log_error "ipfs-api container is not running"
    exit 1
fi

# Check snapshot file exists
if [ ! -f "$SNAPSHOT_FILE" ]; then
    log_error "No snapshot file found at $SNAPSHOT_FILE"
    log "This likely means snapshots are not being built."
    log "Check the API service logs: docker logs ipfs-api"
    exit 1
fi

# Read latest snapshot metadata
SNAPSHOT_CID=$(jq -r '.cid' "$SNAPSHOT_FILE" 2>/dev/null)
SNAPSHOT_SEQ=$(jq -r '.seq' "$SNAPSHOT_FILE" 2>/dev/null)
SNAPSHOT_TS=$(jq -r '.ts' "$SNAPSHOT_FILE" 2>/dev/null)
SNAPSHOT_COUNT=$(jq -r '.count' "$SNAPSHOT_FILE" 2>/dev/null)

if [ -z "$SNAPSHOT_CID" ] || [ "$SNAPSHOT_CID" = "null" ]; then
    log_error "Could not read snapshot CID from $SNAPSHOT_FILE"
    exit 1
fi

log "Latest snapshot metadata:"
log "  CID:       $SNAPSHOT_CID"
log "  Sequence:  $SNAPSHOT_SEQ"
log "  Timestamp: $SNAPSHOT_TS"
log "  Entities:  $SNAPSHOT_COUNT"
log ""

# Export to CAR using Python DR module
log "Exporting snapshot to CAR format..."
log "This may take 1-2 minutes for large snapshots..."
log ""

if docker exec ipfs-api python3 -m dr.export_car "$SNAPSHOT_CID" >> "$LOG_FILE" 2>&1; then
    log_success "CAR export completed successfully"
else
    log_error "CAR export failed"
    log "Check logs in container: docker exec ipfs-api cat /app/logs/snapshot-build.log"
    exit 1
fi

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log_success "Daily backup completed successfully"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
