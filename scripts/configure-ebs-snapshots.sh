#!/bin/bash
# Configure AWS Data Lifecycle Manager for automated EBS snapshots
# Run this once during initial setup

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  EBS Snapshot Configuration${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Configuration
RETENTION_COUNT=7  # Keep 7 daily snapshots
SNAPSHOT_TIME="03:00"  # 3 AM UTC (after daily CAR export)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="us-east-1"

# Check if DLM service role exists, create if not
ROLE_NAME="AWSDataLifecycleManagerDefaultRole"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/service-role/${ROLE_NAME}"

echo -e "${BLUE}Step 1/3: Checking DLM service role...${NC}"
if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
    echo -e "${YELLOW}DLM service role already exists${NC}"
else
    echo -e "Creating DLM service role..."

    # Create trust policy
    cat > /tmp/dlm-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "dlm.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    # Create role
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file:///tmp/dlm-trust-policy.json \
        --description "Default service role for AWS Data Lifecycle Manager"

    # Attach managed policy
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"

    rm /tmp/dlm-trust-policy.json
    echo -e "${GREEN}✓ DLM service role created${NC}"

    # Wait for role to propagate
    sleep 10
fi
echo ""

# Create lifecycle policy
echo -e "${BLUE}Step 2/3: Creating DLM lifecycle policy...${NC}"

# Create policy document (just the PolicyDetails part)
cat > /tmp/dlm-policy.json << EOF
{
  "ResourceTypes": ["VOLUME"],
  "TargetTags": [
    {
      "Key": "Name",
      "Value": "arke-ipfs-server"
    }
  ],
  "Schedules": [
    {
      "Name": "DailySnapshot",
      "CreateRule": {
        "Interval": 24,
        "IntervalUnit": "HOURS",
        "Times": ["$SNAPSHOT_TIME"]
      },
      "RetainRule": {
        "Count": $RETENTION_COUNT
      },
      "TagsToAdd": [
        {
          "Key": "SnapshotType",
          "Value": "DLM-Automated"
        },
        {
          "Key": "Project",
          "Value": "arke-ipfs"
        }
      ],
      "CopyTags": true
    }
  ]
}
EOF

# Check if policy already exists
EXISTING_POLICY=$(aws dlm get-lifecycle-policies \
    --region "$REGION" \
    --query "Policies[?Description=='Daily EBS snapshots for Arke IPFS server'].PolicyId" \
    --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_POLICY" ]; then
    echo -e "${YELLOW}DLM policy already exists: $EXISTING_POLICY${NC}"
    echo -e "${YELLOW}Updating existing policy...${NC}"

    aws dlm update-lifecycle-policy \
        --region "$REGION" \
        --policy-id "$EXISTING_POLICY" \
        --execution-role-arn "$ROLE_ARN" \
        --state ENABLED \
        --policy-details file:///tmp/dlm-policy.json

    POLICY_ID="$EXISTING_POLICY"
    echo -e "${GREEN}✓ Policy updated${NC}"
else
    POLICY_ID=$(aws dlm create-lifecycle-policy \
        --region "$REGION" \
        --execution-role-arn "$ROLE_ARN" \
        --description "Daily EBS snapshots for Arke IPFS server" \
        --state ENABLED \
        --policy-details file:///tmp/dlm-policy.json \
        --query 'PolicyId' \
        --output text)

    echo -e "${GREEN}✓ Lifecycle policy created${NC}"
fi

rm /tmp/dlm-policy.json
echo -e "  Policy ID: $POLICY_ID"
echo ""

# Tag the volume (if instance ID provided or found)
echo -e "${BLUE}Step 3/3: Tagging EBS volume...${NC}"

# Find instance by name tag
INSTANCE_ID=$(aws ec2 describe-instances \
    --region "$REGION" \
    --filters "Name=tag:Name,Values=arke-ipfs-server" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text 2>/dev/null || echo "None")

if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
    echo -e "${YELLOW}No running instance found with tag Name=arke-ipfs-server${NC}"
    echo -e "${YELLOW}You can manually tag the volume later with: Name=arke-ipfs-server${NC}"
else
    # Get volume ID
    VOLUME_ID=$(aws ec2 describe-instances \
        --region "$REGION" \
        --instance-ids "$INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' \
        --output text)

    # Check if volume already has the tag
    CURRENT_TAG=$(aws ec2 describe-volumes \
        --region "$REGION" \
        --volume-ids "$VOLUME_ID" \
        --query "Volumes[0].Tags[?Key=='Name'].Value" \
        --output text)

    if [ "$CURRENT_TAG" = "arke-ipfs-server" ]; then
        echo -e "${YELLOW}Volume already tagged correctly${NC}"
    else
        # Tag the volume
        aws ec2 create-tags \
            --region "$REGION" \
            --resources "$VOLUME_ID" \
            --tags Key=Name,Value=arke-ipfs-server

        echo -e "${GREEN}✓ Volume tagged${NC}"
    fi

    echo -e "  Instance: $INSTANCE_ID"
    echo -e "  Volume: $VOLUME_ID"
fi
echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  EBS Snapshot Configuration Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Schedule: Daily at $SNAPSHOT_TIME UTC"
echo -e "  Retention: $RETENTION_COUNT snapshots"
echo -e "  Target: Volumes tagged with Name=arke-ipfs-server"
echo ""
echo -e "${GREEN}What happens next:${NC}"
echo -e "  - DLM will create first snapshot at next $SNAPSHOT_TIME UTC"
echo -e "  - Old snapshots auto-delete after $RETENTION_COUNT days"
echo -e "  - Snapshots tagged with SnapshotType=DLM-Automated"
echo ""
echo -e "${GREEN}Cost Estimate:${NC}"
echo -e "  ~$0.05/GB/month × 30GB × $RETENTION_COUNT snapshots"
echo -e "  ≈ $10.50/month (incremental, actual cost likely lower)"
echo ""
echo -e "${GREEN}Verify:${NC}"
echo -e "  Wait until $SNAPSHOT_TIME UTC tomorrow, then run:"
echo -e "  ${YELLOW}aws ec2 describe-snapshots --owner-ids self --region $REGION${NC}"
echo ""
