#!/bin/bash
# Verify entity is fully accessible after restore

set -euo pipefail

IPFS_API="${IPFS_API:-http://localhost:5001/api/v0}"
PI="$1"

# Extract shard path
shard1="${PI:0:2}"
shard2="${PI:2:2}"
tip_path="/arke/index/$shard1/$shard2/${PI}.tip"

echo "Verifying entity: $PI"
echo ""

# 1. Read tip file
echo "1. Reading .tip file from MFS..."
TIP=$(curl -sf -X POST "$IPFS_API/files/read?arg=$tip_path" | tr -d '\n')
echo "   Tip CID: $TIP"
echo ""

# 2. Get manifest
echo "2. Fetching manifest..."
MANIFEST=$(curl -sf -X POST "$IPFS_API/dag/get?arg=$TIP")
echo "$MANIFEST" | jq .
echo ""

# 3. Access metadata component
echo "3. Accessing metadata component..."
METADATA_CID=$(echo "$MANIFEST" | jq -r '.components.metadata["/"]')
echo "   Metadata CID: $METADATA_CID"
METADATA=$(curl -sf -X POST "$IPFS_API/cat?arg=$METADATA_CID")
echo "$METADATA" | jq .
echo ""

# 4. Verify image component
echo "4. Verifying image component..."
IMAGE_CID=$(echo "$MANIFEST" | jq -r '.components.image_page_001["/"]')
echo "   Image CID: $IMAGE_CID"
IMAGE_STAT=$(curl -sf -X POST "$IPFS_API/block/stat?arg=$IMAGE_CID")
echo "$IMAGE_STAT" | jq .
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ Entity fully accessible!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
