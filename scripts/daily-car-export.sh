#!/bin/bash
# Daily CAR export with retention and S3 upload
# Runs: build-snapshot.sh → export-car.sh → cleanup old CARs → upload to S3

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"
SNAPSHOT_DIR="$PROJECT_DIR/snapshots"
LOG_FILE="/var/log/arke-backup.log"
RETENTION_DAYS=3

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Logging function
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
log "Starting daily CAR backup process"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Ensure directories exist
mkdir -p "$BACKUP_DIR" "$SNAPSHOT_DIR"

# Step 1: Build snapshot
log "Step 1/4: Building snapshot..."
if CONTAINER_NAME=ipfs-node-prod "$SCRIPT_DIR/build-snapshot.sh" >> "$LOG_FILE" 2>&1; then
    log_success "Snapshot built successfully"
else
    log_error "Failed to build snapshot"
    exit 1
fi

# Step 2: Export to CAR
log "Step 2/4: Exporting CAR file..."
if CONTAINER_NAME=ipfs-node-prod "$SCRIPT_DIR/export-car.sh" >> "$LOG_FILE" 2>&1; then
    log_success "CAR file exported successfully"
    LATEST_CAR=$(ls -t "$BACKUP_DIR"/arke-*.car 2>/dev/null | head -1)
    if [ -n "$LATEST_CAR" ]; then
        CAR_SIZE=$(du -h "$LATEST_CAR" | cut -f1)
        log "  File: $(basename "$LATEST_CAR") (Size: $CAR_SIZE)"
    fi
else
    log_error "Failed to export CAR file"
    exit 1
fi

# Step 3: Clean up old CAR files (keep last 3 days)
log "Step 3/4: Cleaning up old backups (retention: $RETENTION_DAYS days)..."
DELETED_COUNT=0

# Find and delete old CAR files
while IFS= read -r old_file; do
    if [ -n "$old_file" ]; then
        log "  Deleting: $(basename "$old_file")"
        rm -f "$old_file"
        DELETED_COUNT=$((DELETED_COUNT + 1))
    fi
done < <(find "$BACKUP_DIR" -name "arke-*.car" -mtime +$RETENTION_DAYS 2>/dev/null)

# Find and delete old metadata JSON files
while IFS= read -r old_file; do
    if [ -n "$old_file" ]; then
        log "  Deleting: $(basename "$old_file")"
        rm -f "$old_file"
    fi
done < <(find "$BACKUP_DIR" -name "arke-*.json" -mtime +$RETENTION_DAYS 2>/dev/null)

if [ $DELETED_COUNT -eq 0 ]; then
    log "  No old backups to delete"
else
    log_success "Deleted $DELETED_COUNT old backup(s)"
fi

# Show current backup count
CURRENT_COUNT=$(ls -1 "$BACKUP_DIR"/arke-*.car 2>/dev/null | wc -l)
log "  Current backups: $CURRENT_COUNT file(s)"

# Step 4: Upload to S3 (if configured)
log "Step 4/4: Uploading to S3..."
if command -v aws &> /dev/null; then
    if [ -n "$LATEST_CAR" ] && [ -f "$LATEST_CAR" ]; then
        if "$SCRIPT_DIR/upload-to-s3.sh" "$LATEST_CAR" >> "$LOG_FILE" 2>&1; then
            log_success "CAR file uploaded to S3"
        else
            log_error "Failed to upload to S3 (continuing anyway)"
        fi
    else
        log_error "No CAR file to upload"
    fi
else
    log "${YELLOW}AWS CLI not installed, skipping S3 upload${NC}"
fi

# Summary
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log_success "Daily backup completed successfully"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
