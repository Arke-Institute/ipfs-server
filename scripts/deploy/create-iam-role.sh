#!/bin/bash
# Create IAM role and policy for EC2 to upload backups to S3
# Run this once during initial setup

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  IAM Role Setup for S3 Backup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

POLICY_NAME="arke-ipfs-s3-backup-policy"
ROLE_NAME="arke-ipfs-ec2-backup-role"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="arke-ipfs-backups-${ACCOUNT_ID}"

echo -e "Policy: ${YELLOW}$POLICY_NAME${NC}"
echo -e "Role: ${YELLOW}$ROLE_NAME${NC}"
echo -e "Bucket: ${YELLOW}$BUCKET_NAME${NC}"
echo ""

# Create IAM policy document
echo -e "${BLUE}Step 1/5: Creating IAM policy document...${NC}"
cat > /tmp/iam-s3-backup-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowS3Upload",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/daily/*"
    },
    {
      "Sid": "AllowS3List",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}"
    },
    {
      "Sid": "AllowGetCallerIdentity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
EOF
echo -e "${GREEN}✓ Policy document created${NC}"
echo ""

# Create IAM policy (or get existing)
echo -e "${BLUE}Step 2/5: Creating IAM policy...${NC}"
POLICY_ARN=$(aws iam list-policies --query "Policies[?PolicyName=='$POLICY_NAME'].Arn" --output text)

if [ -n "$POLICY_ARN" ]; then
    echo -e "${YELLOW}Policy already exists: $POLICY_ARN${NC}"
    echo -e "${YELLOW}Updating policy with new version...${NC}"

    # Create new policy version
    aws iam create-policy-version \
        --policy-arn "$POLICY_ARN" \
        --policy-document file:///tmp/iam-s3-backup-policy.json \
        --set-as-default
    echo -e "${GREEN}✓ Policy updated${NC}"
else
    POLICY_ARN=$(aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document file:///tmp/iam-s3-backup-policy.json \
        --description "Allow Arke IPFS EC2 instance to upload backups to S3" \
        --query 'Policy.Arn' \
        --output text)
    echo -e "${GREEN}✓ Policy created${NC}"
fi
echo -e "  ARN: $POLICY_ARN"
echo ""

rm /tmp/iam-s3-backup-policy.json

# Create trust policy for EC2
echo -e "${BLUE}Step 3/5: Creating IAM role...${NC}"
cat > /tmp/trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create role (or skip if exists)
if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
    echo -e "${YELLOW}Role already exists: $ROLE_NAME${NC}"
else
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file:///tmp/trust-policy.json \
        --description "IAM role for Arke IPFS EC2 instance to upload backups to S3"
    echo -e "${GREEN}✓ Role created${NC}"
fi

rm /tmp/trust-policy.json
echo ""

# Attach policy to role
echo -e "${BLUE}Step 4/5: Attaching policy to role...${NC}"
if aws iam list-attached-role-policies --role-name "$ROLE_NAME" --query "AttachedPolicies[?PolicyArn=='$POLICY_ARN']" --output text | grep -q "$POLICY_ARN"; then
    echo -e "${YELLOW}Policy already attached to role${NC}"
else
    aws iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "$POLICY_ARN"
    echo -e "${GREEN}✓ Policy attached${NC}"
fi
echo ""

# Create instance profile (or skip if exists)
echo -e "${BLUE}Step 5/5: Creating instance profile...${NC}"
if aws iam get-instance-profile --instance-profile-name "$ROLE_NAME" &> /dev/null; then
    echo -e "${YELLOW}Instance profile already exists: $ROLE_NAME${NC}"
else
    aws iam create-instance-profile \
        --instance-profile-name "$ROLE_NAME"

    # Wait for instance profile to be available
    sleep 2

    # Add role to instance profile
    aws iam add-role-to-instance-profile \
        --instance-profile-name "$ROLE_NAME" \
        --role-name "$ROLE_NAME"

    echo -e "${GREEN}✓ Instance profile created${NC}"
fi
echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  IAM Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Resources Created:${NC}"
echo -e "  Policy: $POLICY_NAME"
echo -e "  Role: $ROLE_NAME"
echo -e "  Instance Profile: $ROLE_NAME"
echo ""
echo -e "${GREEN}Permissions Granted:${NC}"
echo -e "  ✓ Upload to: s3://$BUCKET_NAME/daily/*"
echo -e "  ✓ List bucket: s3://$BUCKET_NAME"
echo ""
echo -e "${GREEN}Next Steps:${NC}"
echo -e "  1. Attach to existing instance:"
echo -e "     ${YELLOW}./scripts/attach-iam-to-instance.sh i-0443444abcd3ed689${NC}"
echo ""
echo -e "  2. Or use in new deployments (already configured in deploy-ec2.sh)"
echo ""
