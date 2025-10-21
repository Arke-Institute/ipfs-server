#!/bin/bash
# Upload CAR file to S3 with metadata
# Usage: ./upload-to-s3.sh <car-file-path>

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <car-file-path>"
    echo "Example: $0 /path/to/backups/arke-2-20251019.car"
    exit 1
fi

CAR_FILE="$1"
METADATA_FILE="${CAR_FILE%.car}.json"

# Validate file exists
if [ ! -f "$CAR_FILE" ]; then
    echo "Error: CAR file not found: $CAR_FILE"
    exit 1
fi

# Get AWS account ID for bucket name
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCOUNT_ID" ]; then
    echo "Error: Failed to get AWS account ID. Is AWS CLI configured?"
    exit 1
fi

BUCKET_NAME="arke-ipfs-backups-${ACCOUNT_ID}"
S3_PREFIX="daily/"
REGION="us-east-1"

# Check if bucket exists
if ! aws s3 ls "s3://$BUCKET_NAME" --region "$REGION" &> /dev/null; then
    echo "Error: S3 bucket '$BUCKET_NAME' does not exist"
    echo "Run scripts/create-s3-backup-bucket.sh first to create it"
    exit 1
fi

# Get file size for logging
FILE_SIZE=$(du -h "$CAR_FILE" | cut -f1)

echo "Uploading CAR file to S3..."
echo "  Source: $CAR_FILE (${FILE_SIZE})"
echo "  Destination: s3://$BUCKET_NAME/$S3_PREFIX$(basename "$CAR_FILE")"

# Upload CAR file with metadata tags
aws s3 cp "$CAR_FILE" "s3://$BUCKET_NAME/$S3_PREFIX$(basename "$CAR_FILE")" \
    --region "$REGION" \
    --storage-class STANDARD \
    --metadata "source=arke-ipfs-ec2,backup-type=automated,upload-date=$(date -u +%Y-%m-%d)" \
    --no-progress

echo "✓ CAR file uploaded successfully"

# Upload metadata JSON if exists
if [ -f "$METADATA_FILE" ]; then
    echo "Uploading metadata JSON..."
    aws s3 cp "$METADATA_FILE" "s3://$BUCKET_NAME/$S3_PREFIX$(basename "$METADATA_FILE")" \
        --region "$REGION" \
        --no-progress
    echo "✓ Metadata uploaded successfully"
fi

echo ""
echo "S3 Upload Summary:"
echo "  Bucket: $BUCKET_NAME"
echo "  Files: $(basename "$CAR_FILE")"
if [ -f "$METADATA_FILE" ]; then
    echo "         $(basename "$METADATA_FILE")"
fi
echo "  Lifecycle: Standard (7 days) → Glacier (90 days) → Delete"
