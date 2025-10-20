#!/bin/bash
# Create S3 bucket for CAR file backups with lifecycle policy
# Run this once during initial setup

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  S3 Backup Bucket Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ -z "$ACCOUNT_ID" ]; then
    echo -e "${YELLOW}Error: Failed to get AWS account ID. Is AWS CLI configured?${NC}"
    exit 1
fi

BUCKET_NAME="arke-ipfs-backups-${ACCOUNT_ID}"
REGION="us-east-1"

echo -e "Bucket name: ${YELLOW}$BUCKET_NAME${NC}"
echo -e "Region: ${YELLOW}$REGION${NC}"
echo ""

# Check if bucket already exists
if aws s3 ls "s3://$BUCKET_NAME" --region "$REGION" &> /dev/null; then
    echo -e "${YELLOW}Bucket already exists: $BUCKET_NAME${NC}"
    echo -e "${YELLOW}Skipping creation. Updating policies...${NC}"
    echo ""
else
    # Create bucket
    echo -e "${BLUE}Step 1/5: Creating S3 bucket...${NC}"
    aws s3api create-bucket \
        --bucket "$BUCKET_NAME" \
        --region "$REGION"
    echo -e "${GREEN}✓ Bucket created${NC}"
    echo ""
fi

# Enable versioning (safety net for accidental deletion)
echo -e "${BLUE}Step 2/5: Enabling versioning...${NC}"
aws s3api put-bucket-versioning \
    --bucket "$BUCKET_NAME" \
    --region "$REGION" \
    --versioning-configuration Status=Enabled
echo -e "${GREEN}✓ Versioning enabled${NC}"
echo ""

# Enable encryption
echo -e "${BLUE}Step 3/5: Enabling encryption...${NC}"
aws s3api put-bucket-encryption \
    --bucket "$BUCKET_NAME" \
    --region "$REGION" \
    --server-side-encryption-configuration '{
        "Rules": [{
            "ApplyServerSideEncryptionByDefault": {
                "SSEAlgorithm": "AES256"
            },
            "BucketKeyEnabled": true
        }]
    }'
echo -e "${GREEN}✓ Encryption enabled (AES256)${NC}"
echo ""

# Lifecycle policy: Standard → Glacier (7d) → Delete (90d)
echo -e "${BLUE}Step 4/5: Configuring lifecycle policy...${NC}"
cat > /tmp/s3-lifecycle.json << 'EOF'
{
    "Rules": [
        {
            "ID": "daily-car-lifecycle",
            "Status": "Enabled",
            "Filter": {
                "Prefix": "daily/"
            },
            "Transitions": [
                {
                    "Days": 7,
                    "StorageClass": "GLACIER"
                }
            ],
            "Expiration": {
                "Days": 90
            }
        }
    ]
}
EOF

aws s3api put-bucket-lifecycle-configuration \
    --bucket "$BUCKET_NAME" \
    --region "$REGION" \
    --lifecycle-configuration file:///tmp/s3-lifecycle.json

rm /tmp/s3-lifecycle.json
echo -e "${GREEN}✓ Lifecycle policy configured${NC}"
echo -e "  - Days 0-7: S3 Standard"
echo -e "  - Days 7-90: Glacier"
echo -e "  - Day 90+: Auto-delete"
echo ""

# Block public access (security)
echo -e "${BLUE}Step 5/5: Blocking public access...${NC}"
aws s3api put-public-access-block \
    --bucket "$BUCKET_NAME" \
    --region "$REGION" \
    --public-access-block-configuration \
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
echo -e "${GREEN}✓ Public access blocked${NC}"
echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  S3 Bucket Ready!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Bucket Details:${NC}"
echo -e "  Name: $BUCKET_NAME"
echo -e "  Region: $REGION"
echo -e "  URL: s3://$BUCKET_NAME/daily/"
echo ""
echo -e "${GREEN}Next Steps:${NC}"
echo -e "  1. Run: ${YELLOW}./scripts/create-iam-role.sh${NC}"
echo -e "  2. Attach IAM role to EC2 instance"
echo -e "  3. Test upload: ${YELLOW}./scripts/upload-to-s3.sh backups/arke-*.car${NC}"
echo ""
echo -e "${GREEN}Cost Estimate:${NC}"
echo -e "  ~$2-5/month for typical usage (15MB daily backups)"
echo ""
