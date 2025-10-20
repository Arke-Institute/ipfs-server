#!/bin/bash
# Attach IAM role to existing EC2 instance
# Usage: ./attach-iam-to-instance.sh <instance-id>

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <instance-id>"
    echo "Example: $0 i-0443444abcd3ed689"
    exit 1
fi

INSTANCE_ID="$1"
ROLE_NAME="arke-ipfs-ec2-backup-role"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Attaching IAM role to EC2 instance...${NC}"
echo -e "  Instance: ${YELLOW}$INSTANCE_ID${NC}"
echo -e "  Role: ${YELLOW}$ROLE_NAME${NC}"
echo ""

# Check if instance already has an IAM role
CURRENT_PROFILE=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].IamInstanceProfile.Arn' \
    --output text 2>/dev/null || echo "None")

if [ "$CURRENT_PROFILE" != "None" ] && [ -n "$CURRENT_PROFILE" ]; then
    echo -e "${YELLOW}Instance already has IAM role attached${NC}"
    echo -e "  Current: $CURRENT_PROFILE"
    echo -e ""
    read -p "Do you want to replace it? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi

    # Disassociate current role
    ASSOCIATION_ID=$(aws ec2 describe-iam-instance-profile-associations \
        --filters "Name=instance-id,Values=$INSTANCE_ID" \
        --query 'IamInstanceProfileAssociations[0].AssociationId' \
        --output text)

    echo "Removing current IAM role association..."
    aws ec2 disassociate-iam-instance-profile \
        --association-id "$ASSOCIATION_ID"

    # Wait for disassociation
    sleep 5
fi

# Associate new role
echo "Attaching IAM role..."
aws ec2 associate-iam-instance-profile \
    --instance-id "$INSTANCE_ID" \
    --iam-instance-profile Name="$ROLE_NAME"

echo -e "${GREEN}✓ IAM role attached successfully${NC}"
echo ""
echo -e "${GREEN}Next Steps:${NC}"
echo -e "  1. SSH into instance and verify AWS CLI works:"
echo -e "     ${YELLOW}aws s3 ls${NC}"
echo ""
echo -e "  2. Test S3 upload:"
echo -e "     ${YELLOW}./scripts/upload-to-s3.sh backups/arke-*.car${NC}"
echo ""
