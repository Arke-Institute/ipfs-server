#!/bin/bash

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Auto-Snapshot Test (Threshold = 2)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

# Check initial state
echo -e "${YELLOW}Step 1: Checking initial state...${NC}"
INITIAL_STATE=$(curl -s http://localhost:3000/index-pointer)
INITIAL_RECENT_COUNT=$(echo "$INITIAL_STATE" | jq -r '.recent_count')
INITIAL_SNAPSHOT_SEQ=$(echo "$INITIAL_STATE" | jq -r '.snapshot_seq')
INITIAL_TOTAL=$(echo "$INITIAL_STATE" | jq -r '.total_count')

echo "  Recent count: $INITIAL_RECENT_COUNT"
echo "  Snapshot seq: $INITIAL_SNAPSHOT_SEQ"
echo "  Total count: $INITIAL_TOTAL"
echo

# Helper function to create a test entity
create_entity() {
  local PI=$1
  local NAME=$2

  echo -e "${YELLOW}Creating entity: $NAME (PI: $PI)${NC}"

  # 1. Create metadata
  METADATA=$(echo "{\"name\": \"$NAME\", \"type\": \"test\"}" | \
    curl -s -X POST -F "file=@-" \
      "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=false" | \
    jq -r '.Cid["/"]')
  echo "  Metadata CID: $METADATA"

  # 2. Create manifest
  MANIFEST=$(cat <<EOF | \
    curl -s -X POST -F "file=@-" \
      "http://localhost:5001/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true" | \
    jq -r '.Cid["/"]'
{
  "schema": "arke/manifest@v1",
  "pi": "$PI",
  "ver": 1,
  "ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "prev": null,
  "components": {
    "metadata": {"/": "$METADATA"}
  },
  "children_pi": [],
  "note": "Auto-snapshot test entity"
}
EOF
)
  echo "  Manifest CID: $MANIFEST"

  # 3. Create .tip file in MFS
  SHARD1="${PI:0:2}"
  SHARD2="${PI:2:2}"
  TIP_PATH="/arke/index/$SHARD1/$SHARD2/${PI}.tip"

  curl -s -X POST "http://localhost:5001/api/v0/files/mkdir?arg=/arke/index/$SHARD1/$SHARD2&parents=true" > /dev/null
  echo "$MANIFEST" | curl -s -X POST -F "file=@-" \
    "http://localhost:5001/api/v0/files/write?arg=$TIP_PATH&create=true&truncate=true" > /dev/null
  echo "  Tip file: $TIP_PATH"

  # 4. Append to chain via API
  CHAIN_RESULT=$(curl -s -X POST http://localhost:3000/chain/append \
    -H "Content-Type: application/json" \
    -d "{\"pi\": \"$PI\", \"tip_cid\": \"$MANIFEST\", \"ver\": 1}")

  CHAIN_CID=$(echo "$CHAIN_RESULT" | jq -r '.cid')
  echo "  Chain CID: $CHAIN_CID"
  echo

  # Give API time to process
  sleep 1
}

# Create Entity 1
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Entity 1${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
create_entity "01TEST0001AUTOSNAPSHOT" "Auto-Snapshot Test 1"

# Check state after entity 1
STATE_1=$(curl -s http://localhost:3000/index-pointer)
RECENT_1=$(echo "$STATE_1" | jq -r '.recent_count')
SNAPSHOT_1=$(echo "$STATE_1" | jq -r '.snapshot_seq')
echo -e "${BLUE}State after Entity 1:${NC}"
echo "  Recent count: $RECENT_1 (threshold: 2)"
echo "  Snapshot seq: $SNAPSHOT_1"
echo

# Create Entity 2 (should trigger auto-snapshot)
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Entity 2 (Should Trigger Auto-Snapshot!)${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
create_entity "01TEST0002AUTOSNAPSHOT" "Auto-Snapshot Test 2"

# Wait for snapshot to build
echo -e "${YELLOW}Waiting 15 seconds for snapshot to build...${NC}"
for i in {15..1}; do
  echo -ne "  $i seconds remaining...\r"
  sleep 1
done
echo

# Check state after entity 2
STATE_2=$(curl -s http://localhost:3000/index-pointer)
RECENT_2=$(echo "$STATE_2" | jq -r '.recent_count')
SNAPSHOT_2=$(echo "$STATE_2" | jq -r '.snapshot_seq')
SNAPSHOT_COUNT_2=$(echo "$STATE_2" | jq -r '.snapshot_count')
TOTAL_2=$(echo "$STATE_2" | jq -r '.total_count')

echo -e "${BLUE}State after Entity 2 (and auto-snapshot):${NC}"
echo "  Recent count: $RECENT_2 (should be 0 if snapshot built)"
echo "  Snapshot seq: $SNAPSHOT_2 (should be +1 from initial)"
echo "  Snapshot count: $SNAPSHOT_COUNT_2"
echo "  Total count: $TOTAL_2"
echo

# Check if snapshot was created
if [ -f "snapshots/latest.json" ]; then
  SNAPSHOT_INFO=$(cat snapshots/latest.json)
  SNAPSHOT_CID=$(echo "$SNAPSHOT_INFO" | jq -r '.cid')
  SNAPSHOT_SEQ=$(echo "$SNAPSHOT_INFO" | jq -r '.seq')
  SNAPSHOT_ENTITIES=$(echo "$SNAPSHOT_INFO" | jq -r '.count')

  echo -e "${GREEN}✓ Snapshot file exists!${NC}"
  echo "  CID: $SNAPSHOT_CID"
  echo "  Sequence: $SNAPSHOT_SEQ"
  echo "  Entities: $SNAPSHOT_ENTITIES"
  echo

  # Verify snapshot structure
  echo -e "${YELLOW}Verifying snapshot structure...${NC}"
  SNAPSHOT_DATA=$(docker exec ipfs-node ipfs dag get "$SNAPSHOT_CID")
  echo "$SNAPSHOT_DATA" | jq '{schema, seq, total_count, entries_head}'
  echo
else
  echo -e "${RED}✗ No snapshot file found!${NC}"
  echo
fi

# Check for CAR file
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  CAR File Check${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

if ls backups/arke-*.car 1> /dev/null 2>&1; then
  LATEST_CAR=$(ls -t backups/arke-*.car | head -1)
  CAR_SIZE=$(ls -lh "$LATEST_CAR" | awk '{print $5}')
  echo -e "${YELLOW}⚠ CAR files exist (but NOT auto-created):${NC}"
  echo "  Latest: $LATEST_CAR"
  echo "  Size: $CAR_SIZE"
  echo
  echo -e "${BLUE}Note: Auto-snapshot only triggers snapshot build, NOT CAR export.${NC}"
  echo -e "${BLUE}      Run './scripts/export-car.sh' manually to create a CAR file.${NC}"
else
  echo -e "${BLUE}No CAR files found (as expected).${NC}"
  echo -e "${BLUE}Auto-snapshot only triggers snapshot build, NOT CAR export.${NC}"
  echo -e "${BLUE}Run './scripts/export-car.sh' to create a CAR file from the snapshot.${NC}"
fi
echo

# Summary
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Test Summary${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

if [ "$RECENT_2" -eq 0 ] && [ "$SNAPSHOT_2" -gt "$INITIAL_SNAPSHOT_SEQ" ]; then
  echo -e "${GREEN}✓ AUTO-SNAPSHOT SUCCESSFUL!${NC}"
  echo "  • Threshold reached (2 entities)"
  echo "  • Snapshot automatically built"
  echo "  • Recent count reset to 0"
  echo "  • Snapshot sequence incremented"
else
  echo -e "${RED}✗ AUTO-SNAPSHOT FAILED${NC}"
  echo "  • Recent count: $RECENT_2 (expected: 0)"
  echo "  • Snapshot seq: $SNAPSHOT_2 (expected: > $INITIAL_SNAPSHOT_SEQ)"
fi

echo
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Check API logs: docker compose logs ipfs-api"
echo "  2. Export CAR manually: ./scripts/export-car.sh"
echo "  3. Reset threshold: Edit api/config.py (REBUILD_THRESHOLD back to 10000)"
echo
