Option 1: Detailed Implementation Plan

  Executive Summary

  Current State:
  - ✅ httpx already in requirements.txt
  - ✅ DR scripts in scripts/dr/ (Python, ready to use)
  - ✅ ipfs-api container has Python 3.11
  - ⚠️ Auto-snapshot calls OLD build-snapshot.sh (bash script)
  - ❌ No Docker socket access for docker exec
  - ❌ DR scripts not accessible inside container

  Target State:
  - ✅ DR Python scripts accessible inside ipfs-api container
  - ✅ Docker socket mounted for docker exec ipfs-node-prod
  - ✅ Auto-snapshot uses NEW build-snapshot.py (Python)
  - ✅ Daily cron job runs snapshot + export + S3 upload
  - ✅ All operations logged and monitored

  ---
  Phase 1: File Reorganization & Container Setup

  1.1 Move DR Scripts into API Directory

  Rationale: Scripts should live where they'll be executed (inside ipfs-api container)

  New Structure:
  api/
  ├── dr/                          # NEW: DR scripts module
  │   ├── __init__.py             # Make it a Python package
  │   ├── build_snapshot.py       # Renamed (underscore for Python convention)
  │   ├── export_car.py           # Renamed
  │   ├── restore_from_car.py     # Renamed
  │   ├── verify_snapshot.py      # Renamed
  │   ├── verify_car.py           # Renamed
  │   └── generate_test_data.py   # Renamed
  ├── main.py
  ├── events.py
  ├── requirements.txt
  └── Dockerfile

  Migration Steps:
  # Create DR module
  mkdir -p api/dr
  touch api/dr/__init__.py

  # Copy and rename scripts (keep originals for now)
  cp scripts/dr/build-snapshot.py api/dr/build_snapshot.py
  cp scripts/dr/export-car.py api/dr/export_car.py
  cp scripts/dr/restore-from-car.py api/dr/restore_from_car.py
  cp scripts/dr/verify-snapshot.py api/dr/verify_snapshot.py
  cp scripts/dr/verify-car.py api/dr/verify_car.py
  cp scripts/dr/generate-test-data.py api/dr/generate_test_data.py

  Why reorganize?
  - ✅ Scripts are part of the API service's responsibility
  - ✅ Easier imports: from dr import build_snapshot
  - ✅ All Python code in one place for consistency
  - ✅ Follows Python package conventions

  1.2 Update Dockerfile

  File: api/Dockerfile

  FROM python:3.11-slim

  WORKDIR /app

  # Install system dependencies for DR operations
  # - docker-cli: For docker exec commands to ipfs-node
  # - curl/jq: For direct IPFS API calls (legacy support)
  RUN apt-get update && \
      apt-get install -y \
          curl \
          jq \
          docker.io \
      && rm -rf /var/lib/apt/lists/*

  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt

  # Copy application code (includes dr/ module)
  COPY . .

  CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]

  Changes:
  - ➕ Install docker.io package (provides docker CLI for exec commands)
  - 📝 Document why each dependency exists

  1.3 Update docker-compose.nginx.yml

  File: docker-compose.nginx.yml

  ipfs-api:
    build:
      context: ./api
      dockerfile: Dockerfile
    container_name: ipfs-api
    environment:
      - IPFS_API_URL=http://ipfs:5001/api/v0
      - INDEX_POINTER_PATH=/arke/index-pointer
      - SNAPSHOT_INTERVAL_MINUTES=60
      - AUTO_SNAPSHOT=true
      - SNAPSHOT_TIMEOUT_SECONDS=60
      - CONTAINER_NAME=ipfs-node-prod        # NEW: Tell DR scripts which container
    volumes:
      - ./snapshots:/app/snapshots
      - ./backups:/app/backups
      - /var/run/docker.sock:/var/run/docker.sock  # NEW: Docker socket access
    depends_on:
      - ipfs
    restart: always
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    networks:
      - ipfs-network

  Changes:
  - ➕ Add CONTAINER_NAME=ipfs-node-prod env var
  - ➕ Mount Docker socket: /var/run/docker.sock:/var/run/docker.sock
  - ➖ Remove old scripts mount (scripts now in container image)

  ---
  Phase 2: Update Auto-Snapshot Integration

  2.1 Modify events.py to Use New Python Script

  File: api/events.py

  async def trigger_scheduled_snapshot():
      """
      Triggered by scheduler every N minutes.
      Builds a snapshot if there are entities and no build is already in progress.
      """
      # Check if lock file exists
      lock_file = Path("/tmp/arke-snapshot.lock")
      if lock_file.exists():
          print("⏳ Snapshot build already in progress, skipping")
          return

      # Get current state
      pointer = await index_pointer.get_index_pointer()

      if pointer.total_count == 0:
          print("ℹ️  No entities to snapshot, skipping")
          return

      print(f"⏰ Scheduled snapshot trigger (PIs: {pointer.total_count}, events: {pointer.event_count})")

      # NEW: Use Python DR module instead of bash script
      from dr import build_snapshot

      # Update trigger timestamp
      trigger_time = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
      pointer.last_snapshot_trigger = trigger_time
      await index_pointer.update_index_pointer(pointer, timeout=600.0)

      # Trigger in background thread (avoid blocking async event loop)
      loop = asyncio.get_event_loop()
      loop.run_in_executor(None, _run_snapshot_build, trigger_time, pointer)

  def _run_snapshot_build(trigger_time: str, pointer):
      """
      Run snapshot build in thread pool to avoid blocking async loop.
      This calls the DR Python script directly.
      """
      log_path = Path("/app/logs/snapshot-build.log")
      log_path.parent.mkdir(exist_ok=True)

      try:
          with open(log_path, 'a') as log_file:
              log_file.write(f"\n{'='*60}\n")
              log_file.write(f"[SCHEDULED] Snapshot build at {trigger_time}\n")
              log_file.write(f"Total PIs: {pointer.total_count}\n")
              log_file.write(f"Total events: {pointer.event_count}\n")
              log_file.write(f"{'='*60}\n\n")

              # Run build_snapshot.py as subprocess
              subprocess.run(
                  ["python3", "-m", "dr.build_snapshot"],
                  stdout=log_file,
                  stderr=subprocess.STDOUT,
                  cwd="/app",
                  check=False  # Don't raise on error, just log
              )
          print(f"✅ Snapshot build completed (see {log_path})")
      except Exception as e:
          print(f"❌ Snapshot build failed: {e}")

  Changes:
  - ➖ Remove bash script call: subprocess.Popen([script_path])
  - ➕ Import DR module: from dr import build_snapshot
  - ➕ Run as Python module: python3 -m dr.build_snapshot
  - ✅ Keep background execution (non-blocking)

  ---
  Phase 3: Daily Cron Job Setup

  3.1 Create DR Automation Script

  File: scripts/cron-daily-dr-backup.sh (NEW)

  #!/bin/bash
  set -e

  # Daily DR Backup - Runs inside EC2 host via cron
  # Executes DR Python scripts inside ipfs-api container

  LOG_FILE="/var/log/arke-dr-backup.log"
  PROJECT_DIR="/home/ubuntu/ipfs-server"
  CONTAINER="ipfs-api"

  log() {
      echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] $1" | tee -a "$LOG_FILE"
  }

  log_error() {
      echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] ERROR: $1" | tee -a "$LOG_FILE" >&2
  }

  # Ensure container is running
  if ! docker ps | grep -q "$CONTAINER"; then
      log_error "Container $CONTAINER is not running!"
      exit 1
  fi

  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "Starting daily DR backup"
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Step 1: Build snapshot
  log "Step 1/4: Building snapshot..."
  if docker exec "$CONTAINER" python3 -m dr.build_snapshot >> "$LOG_FILE" 2>&1; then
      log "✅ Snapshot built successfully"
  else
      log_error "Failed to build snapshot"
      exit 1
  fi

  # Step 2: Export CAR
  log "Step 2/4: Exporting CAR file..."
  if docker exec "$CONTAINER" python3 -m dr.export_car >> "$LOG_FILE" 2>&1; then
      log "✅ CAR exported successfully"
      LATEST_CAR=$(ls -t "$PROJECT_DIR/backups"/arke-*.car 2>/dev/null | head -1)
      if [ -n "$LATEST_CAR" ]; then
          CAR_SIZE=$(du -h "$LATEST_CAR" | cut -f1)
          log "  File: $(basename "$LATEST_CAR") (Size: $CAR_SIZE)"
      fi
  else
      log_error "Failed to export CAR"
      exit 1
  fi

  # Step 3: Verify CAR (optional, can disable for speed)
  log "Step 3/4: Verifying CAR integrity..."
  if [ -n "$LATEST_CAR" ]; then
      if docker exec "$CONTAINER" python3 -m dr.verify_car "$LATEST_CAR" >> "$LOG_FILE" 2>&1; then
          log "✅ CAR verified successfully"
      else
          log_error "CAR verification failed (continuing anyway)"
      fi
  fi

  # Step 4: Upload to S3
  log "Step 4/4: Uploading to S3..."
  if [ -n "$LATEST_CAR" ] && [ -f "$LATEST_CAR" ]; then
      if "$PROJECT_DIR/scripts/deploy/upload-to-s3.sh" "$LATEST_CAR" >> "$LOG_FILE" 2>&1; then
          log "✅ Uploaded to S3"
      else
          log_error "S3 upload failed (continuing anyway)"
      fi
  else
      log_error "No CAR file to upload"
  fi

  # Step 5: Cleanup old backups (keep last 7 days)
  log "Step 5/5: Cleaning up old backups (retention: 7 days)..."
  DELETED=0
  while IFS= read -r old_file; do
      log "  Deleting: $(basename "$old_file")"
      rm -f "$old_file"
      DELETED=$((DELETED + 1))
  done < <(find "$PROJECT_DIR/backups" -name "arke-*.car" -mtime +7 2>/dev/null)

  if [ $DELETED -eq 0 ]; then
      log "  No old backups to delete"
  else
      log "  Deleted $DELETED old backup(s)"
  fi

  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "✅ Daily DR backup completed successfully"
  log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  Key Features:
  - ✅ Runs DR scripts via docker exec ipfs-api python3 -m dr.script_name
  - ✅ Comprehensive logging
  - ✅ Error handling and exit codes
  - ✅ CAR verification (optional)
  - ✅ S3 upload integration
  - ✅ Automatic cleanup (7-day retention)

  3.2 Update remote-setup.sh to Install Cron Job

  File: scripts/deploy/remote-setup.sh

  Add at the end:
  # Setup daily DR backup cron job
  echo "Setting up daily DR backup cron..."
  cat > /tmp/arke-cron << 'EOF'
  # Daily DR backup at 2 AM UTC
  0 2 * * * /home/ubuntu/ipfs-server/scripts/cron-daily-dr-backup.sh

  # Weekly full verification (Sunday 3 AM UTC)
  0 3 * * 0 docker exec ipfs-api python3 -m dr.verify_snapshot >> /var/log/arke-dr-verify.log 2>&1
  EOF

  crontab /tmp/arke-cron
  echo "✓ Cron jobs installed"

  3.3 Deprecate Old daily-car-export.sh

  File: scripts/daily-car-export.sh

  Add deprecation notice at top:
  #!/bin/bash
  echo "DEPRECATED: This script has been replaced by cron-daily-dr-backup.sh"
  echo "The new script uses Python DR modules inside the ipfs-api container."
  echo "See: scripts/cron-daily-dr-backup.sh"
  exit 1

  ---
  Phase 4: Testing Strategy

  4.1 Local Development Testing

  Environment Setup:
  # Use docker-compose.yml (local dev environment)
  docker compose down -v  # Clean slate
  docker compose up -d --build

  Test Checklist:

  Test 1: Container Build & Startup
  # Verify Docker CLI available inside container
  docker exec ipfs-api which docker
  # Should output: /usr/bin/docker

  # Verify DR module importable
  docker exec ipfs-api python3 -c "from dr import build_snapshot; print('OK')"
  # Should output: OK

  # Check environment variables
  docker exec ipfs-api env | grep CONTAINER_NAME
  # Should output: CONTAINER_NAME=ipfs-node

  Test 2: Generate Test Data
  # Create test entities
  docker exec ipfs-api python3 -m dr.generate_test_data

  # Verify entities created
  curl http://localhost:3000/index-pointer | jq .total_count
  # Should show: 3

  Test 3: Manual Snapshot Build
  # Trigger snapshot manually
  docker exec ipfs-api python3 -m dr.build_snapshot

  # Verify snapshot created
  ls -lh snapshots/
  # Should see: snapshot-1.json, latest.json

  # Check snapshot content
  cat snapshots/latest.json | jq .
  # Should show: {"cid": "baguqee...", "seq": 1, "count": 3}

  Test 4: CAR Export
  # Export to CAR
  docker exec ipfs-api python3 -m dr.export_car

  # Verify CAR created
  ls -lh backups/
  # Should see: arke-1-YYYYMMDD-HHMMSS.car

  # Check CAR metadata
  cat backups/arke-*.json | jq .cid_counts
  # Should show counts for snapshot, dag_nodes, events

  Test 5: CAR Verification
  # Verify CAR completeness
  docker exec ipfs-api python3 -m dr.verify_car backups/arke-*.car

  # Should output: "✓ All content verified"

  Test 6: Restore from CAR (Nuclear test)
  # Stop and wipe IPFS data
  docker compose down
  docker volume rm ipfs-server_ipfs_data

  # Start fresh
  docker compose up -d
  sleep 10  # Wait for IPFS ready

  # Restore
  CAR_FILE=$(ls backups/arke-*.car | head -1)
  docker exec ipfs-api python3 -m dr.restore_from_car "$CAR_FILE"

  # Verify restoration
  curl http://localhost:3000/index-pointer | jq .total_count
  # Should show: 3 (entities restored)

  # Verify entity accessible
  docker exec ipfs-node ipfs files ls /arke/index/EN/TI/
  # Should list: ENTITY_A00000000000000.tip, etc.

  Test 7: Auto-Snapshot (Scheduler)
  # Set short interval for testing
  docker compose down
  # Edit docker-compose.yml: SNAPSHOT_INTERVAL_MINUTES=1
  docker compose up -d

  # Wait 2 minutes, check logs
  docker logs ipfs-api -f
  # Should see: "⏰ Scheduled snapshot trigger"
  # Should see: "✅ Snapshot build completed"

  # Verify snapshot sequence incremented
  cat snapshots/latest.json | jq .seq
  # Should show: 2 (or higher)

  4.2 Production-Like Testing (EC2 Staging)

  Environment: Use docker-compose.nginx.yml on EC2 instance

  Setup Staging Instance:
  # Deploy to fresh EC2 instance (or existing with --no-restore)
  ./scripts/deploy/setup-instance.sh <STAGING_IP> --upload-backup

  # SSH to instance
  ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<STAGING_IP>

  Test 8: Production Container Setup
  # On EC2 instance
  docker ps | grep ipfs-api
  # Should show: ipfs-api container running

  # Verify Docker socket mounted
  docker exec ipfs-api ls -l /var/run/docker.sock
  # Should show: srw-rw---- (socket file)

  # Test docker exec from inside container
  docker exec ipfs-api docker ps
  # Should list: ipfs-node-prod, ipfs-api, ipfs-nginx

  Test 9: Cron Job Execution
  # Manually run DR backup script (as cron would)
  sudo /home/ubuntu/ipfs-server/scripts/cron-daily-dr-backup.sh

  # Check logs
  tail -100 /var/log/arke-dr-backup.log
  # Should show: All 5 steps completed successfully

  # Verify CAR created
  ls -lh /home/ubuntu/ipfs-server/backups/
  # Should see: Latest CAR file

  Test 10: Cron Scheduler
  # Verify cron installed
  crontab -l
  # Should show: Daily 2 AM job

  # Test cron (wait for next 2 AM UTC, or temporarily modify cron)
  # Option: Change to "*/5 * * * *" (every 5 minutes) for testing
  sudo crontab -e
  # Change: 0 2 * * * to */5 * * * *
  # Wait 5 minutes

  # Check log
  tail -f /var/log/arke-dr-backup.log
  # Should see automated run

  # IMPORTANT: Change cron back to 0 2 * * * after testing!

  Test 11: S3 Upload Integration
  # Verify AWS CLI configured
  aws s3 ls s3://arke-ipfs-backups/
  # Should list: Previous backups (if any)

  # Manually trigger upload
  LATEST_CAR=$(ls -t /home/ubuntu/ipfs-server/backups/arke-*.car | head -1)
  /home/ubuntu/ipfs-server/scripts/deploy/upload-to-s3.sh "$LATEST_CAR"

  # Verify uploaded
  aws s3 ls s3://arke-ipfs-backups/ --human-readable
  # Should show: Newly uploaded CAR

  Test 12: Auto-Snapshot in Production
  # Check FastAPI auto-snapshot status
  docker logs ipfs-api | grep snapshot
  # Should see: "🕐 Starting snapshot scheduler (every 60 minutes)"

  # Manually append event to trigger snapshot condition
  curl -X POST http://localhost:3000/events/append \
    -H "Content-Type: application/json" \
    -d '{"type":"create","pi":"TEST01234567890123456","ver":1,"tip_cid":"bafyreibogus"}'

  # Wait for next scheduled run (or reduce interval temporarily)
  # Check snapshot built
  cat /home/ubuntu/ipfs-server/snapshots/latest.json | jq .

  4.3 Load Testing (31k+ Entities)

  Test 13: Large Dataset Performance
  # Assumption: Production has 31k+ entities

  # Time snapshot build
  time docker exec ipfs-api python3 -m dr.build_snapshot
  # Expected: 5-15 minutes depending on chain length

  # Check snapshot size
  ls -lh snapshots/snapshot-*.json
  # Expected: < 500 bytes/entity (e.g., 15MB for 31k entities)

  # Time CAR export
  time docker exec ipfs-api python3 -m dr.export_car
  # Expected: 10-30 minutes depending on total data size

  # Check CAR size
  ls -lh backups/arke-*.car
  # Expected: Varies by component data (could be GBs)

  ---
  Phase 5: Integration with Existing Automation

  5.1 Auto-Snapshot Flow

  Current Behavior (every 60 minutes):
  1. Scheduler triggers events.trigger_scheduled_snapshot()
  2. Checks lock file (prevents concurrent builds)
  3. Checks entity count > 0
  4. Runs python3 -m dr.build_snapshot in background
  5. Logs to /app/logs/snapshot-build.log

  New Enhancement (optional): Add CAR export on schedule
  # In events.py
  async def trigger_scheduled_backup():
      """
      Extended version: Build snapshot + export CAR.
      Run this less frequently (e.g., daily instead of hourly).
      """
      # Build snapshot first
      await trigger_scheduled_snapshot()

      # Wait for snapshot to complete (check lock file cleared)
      for _ in range(60):  # Wait up to 60 minutes
          if not Path("/tmp/arke-snapshot.lock").exists():
              break
          await asyncio.sleep(60)

      # Export CAR
      loop = asyncio.get_event_loop()
      await loop.run_in_executor(None, _run_car_export)

  def _run_car_export():
      """Run CAR export in thread pool."""
      subprocess.run(
          ["python3", "-m", "dr.export_car"],
          cwd="/app",
          check=False
      )

  Scheduler Configuration:
  # main.py startup
  scheduler.add_job(
      events.trigger_scheduled_snapshot,
      'interval',
      minutes=60,  # Hourly snapshots
      id='snapshot_builder'
  )

  scheduler.add_job(
      events.trigger_scheduled_backup,
      'cron',
      hour=1,  # Daily at 1 AM UTC (before cron at 2 AM)
      id='daily_car_export'
  )

  5.2 Daily Cron Workflow

  Full Daily Backup Workflow (2 AM UTC):
  2:00 AM - Cron triggers
  ├─ Check container running
  ├─ Build snapshot (if needed)
  ├─ Export to CAR
  ├─ Verify CAR integrity
  ├─ Upload to S3
  ├─ Clean old backups (>7 days)
  └─ Log everything

  Coordination with Auto-Snapshot:
  - Auto-snapshot runs hourly (1 AM, 2 AM, 3 AM, etc.)
  - Cron runs at 2 AM
  - If snapshot already fresh (built at 1 AM), build-snapshot.py detects and skips
  - This is handled by comparing event_head in pointer with latest snapshot

  ---
  Phase 6: Monitoring & Alerts

  6.1 Log Monitoring

  Log Files:
  - /var/log/arke-dr-backup.log - Daily cron job logs
  - /app/logs/snapshot-build.log - Auto-snapshot logs (inside container)
  - /var/log/arke-dr-verify.log - Weekly verification logs

  Log Rotation:
  # Add to remote-setup.sh
  cat > /etc/logrotate.d/arke-dr << 'EOF'
  /var/log/arke-dr*.log {
      daily
      rotate 14
      compress
      delaycompress
      missingok
      notifempty
  }
  EOF

  6.2 Health Checks

  Add Health Check Endpoint:
  # api/main.py
  @app.get("/admin/dr/status")
  async def dr_status():
      """Get DR status: last snapshot, last backup, CAR count."""
      latest_snapshot = Path("/app/snapshots/latest.json")
      latest_car = sorted(Path("/app/backups").glob("arke-*.car"), reverse=True)

      status = {
          "snapshot": {
              "exists": latest_snapshot.exists(),
              "last_modified": latest_snapshot.stat().st_mtime if latest_snapshot.exists() else None,
          },
          "backups": {
              "count": len(latest_car),
              "latest": latest_car[0].name if latest_car else None,
              "total_size_mb": sum(f.stat().st_size for f in latest_car) / 1024 / 1024
          },
          "lock_file_exists": Path("/tmp/arke-snapshot.lock").exists()
      }
      return status

  Monitoring Script:
  # Check DR health daily
  curl -s http://localhost:3000/admin/dr/status | jq .

  ---
  Phase 7: Deployment Checklist

  Pre-Deployment

  - Review all code changes
  - Run all local tests (Tests 1-7)
  - Build API container: docker compose build ipfs-api
  - Test locally with docker-compose.yml
  - Test locally with docker-compose.nginx.yml

  Staging Deployment

  - Deploy to staging EC2 instance
  - Run production tests (Tests 8-12)
  - Monitor for 48 hours
  - Verify cron executes successfully (check logs)
  - Verify S3 uploads working

  Production Deployment

  - Schedule maintenance window
  - Backup current production data
  - Deploy updated code:
  cd /home/ubuntu/ipfs-server
  git pull
  docker compose -f docker-compose.nginx.yml build ipfs-api
  docker compose -f docker-compose.nginx.yml up -d
  - Verify containers running: docker ps
  - Test DR scripts manually
  - Verify cron installed: crontab -l
  - Monitor first automated run

  Post-Deployment

  - Monitor logs for 1 week
  - Verify daily backups creating
  - Verify S3 uploads successful
  - Test restore from CAR (on staging)
  - Update documentation

  ---
  Summary: What Changes Where

  | Component      | Current             | New                         | Action                 |
  |----------------|---------------------|-----------------------------|------------------------|
  | DR Scripts     | scripts/dr/*.py     | api/dr/*.py                 | Move & rename          |
  | API Dockerfile | No Docker CLI       | Installs docker.io          | Update                 |
  | docker-compose | No socket mount     | Mounts /var/run/docker.sock | Update                 |
  | Auto-snapshot  | Calls bash script   | Calls Python module         | Update events.py       |
  | Daily backup   | daily-car-export.sh | cron-daily-dr-backup.sh     | Create new             |
  | Cron job       | None                | Daily at 2 AM UTC           | Add to remote-setup.sh |
  | Requirements   | Has httpx           | No change                   | Already satisfied      |

  ---
  Risk Mitigation

  Risk 1: Docker socket security
  - Mitigation: Container runs as non-root, read-only scripts
  - Alternative: Use Docker socket proxy (tecnativa/docker-socket-proxy)

  Risk 2: Snapshot build during high load
  - Mitigation: Lock file prevents concurrent builds
  - Monitoring: Alert if build takes >30 minutes

  Risk 3: Disk space from CAR files
  - Mitigation: 7-day retention, automatic cleanup
  - Monitoring: Alert if /home/ubuntu >80% full

  Risk 4: S3 upload failures
  - Mitigation: Local backups retained, logs capture errors
  - Monitoring: Check S3 bucket daily

  ---
  Next Steps

  Would you like me to:
  1. Start implementation - Begin with Phase 1 (file reorganization)?
  2. Create test plan document - Detailed testing procedures?
  3. Review specific phase - Deep dive into any section?
  4. Discuss alternatives - Explore other aspects?

⏺ I've created a comprehensive implementation plan that covers:

  📋 7-Phase Implementation Plan

  Phase 1: File Reorganization - Move DR scripts into api/dr/ module (proper Python structure)

  Phase 2: Container Setup - Update Dockerfile to install Docker CLI, mount socket in docker-compose

  Phase 3: Auto-Snapshot Integration - Modify events.py to call Python modules instead of bash scripts

  Phase 4: Daily Cron Setup - Create cron-daily-dr-backup.sh for automated daily backups

  Phase 5: Testing Strategy - 13 comprehensive tests covering local dev, staging, and production scenarios

  Phase 6: Integration - How auto-snapshot and daily cron work together

  Phase 7: Monitoring - Log rotation, health checks, alerts

  🎯 Key Insights from Analysis

  Good News:
  - ✅ httpx already in requirements.txt (no dependency changes needed)
  - ✅ Container already has Python 3.11 + curl/jq
  - ✅ Auto-snapshot infrastructure exists (just need to point to new scripts)

  Reorganization Benefits:
  - Moving scripts to api/dr/ makes them proper Python modules
  - Can import: from dr import build_snapshot
  - Follows Python conventions (underscores instead of hyphens)
  - All Python code in one place

  Testing Approach:
  - Local tests with docker-compose.yml (Tests 1-7)
  - Staging EC2 with docker-compose.nginx.yml (Tests 8-12)
  - Production load test with 31k+ entities (Test 13)
  - Nuclear test (wipe and restore from CAR)

  Integration Points:
  - Auto-snapshot runs hourly (current behavior preserved)
  - Daily cron at 2 AM builds + exports + uploads to S3
  - Both use same Python DR modules
  - Lock file prevents conflicts