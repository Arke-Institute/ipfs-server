#!/bin/bash
set -e

# Arke IPFS Server - EC2 Deployment Script
# Creates SSH key, security group, and launches EC2 instance

# Configuration
REGION="us-east-1"
INSTANCE_TYPE="t3.small"
VOLUME_SIZE=30
KEY_NAME="arke-ipfs-key"
SECURITY_GROUP_NAME="arke-ipfs-sg"
INSTANCE_NAME="arke-ipfs-server"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Arke IPFS Server - EC2 Deployment${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check AWS CLI is configured
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${YELLOW}Error: AWS CLI not configured. Run 'aws configure' first.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ AWS CLI configured${NC}"
echo ""

# Step 1: Create SSH Key Pair
echo -e "${BLUE}Step 1: Creating SSH key pair...${NC}"
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" &> /dev/null; then
    echo -e "${YELLOW}Key pair '$KEY_NAME' already exists. Skipping creation.${NC}"
    echo -e "${YELLOW}If you need to recreate it, delete it first with:${NC}"
    echo -e "${YELLOW}  aws ec2 delete-key-pair --key-name $KEY_NAME --region $REGION${NC}"
    echo -e "${YELLOW}  rm ~/.ssh/${KEY_NAME}.pem${NC}"
else
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$REGION" \
        --query 'KeyMaterial' \
        --output text > ~/.ssh/${KEY_NAME}.pem

    chmod 400 ~/.ssh/${KEY_NAME}.pem
    echo -e "${GREEN}✓ Created key pair: ~/.ssh/${KEY_NAME}.pem${NC}"
fi
echo ""

# Step 2: Create Security Group
echo -e "${BLUE}Step 2: Creating security group...${NC}"
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SECURITY_GROUP_NAME" \
    --region "$REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    # Get default VPC ID
    VPC_ID=$(aws ec2 describe-vpcs \
        --filters "Name=is-default,Values=true" \
        --region "$REGION" \
        --query 'Vpcs[0].VpcId' \
        --output text)

    # Create security group
    SG_ID=$(aws ec2 create-security-group \
        --group-name "$SECURITY_GROUP_NAME" \
        --description "Security group for Arke IPFS server" \
        --vpc-id "$VPC_ID" \
        --region "$REGION" \
        --query 'GroupId' \
        --output text)

    # Add SSH rule (port 22)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 22 \
        --cidr 0.0.0.0/0 \
        --region "$REGION" > /dev/null

    # Add IPFS Swarm rule (port 4001)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 4001 \
        --cidr 0.0.0.0/0 \
        --region "$REGION" > /dev/null

    # Add HTTP rule (port 80)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 80 \
        --cidr 0.0.0.0/0 \
        --region "$REGION" > /dev/null

    # Add HTTPS rule (port 443)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 443 \
        --cidr 0.0.0.0/0 \
        --region "$REGION" > /dev/null

    echo -e "${GREEN}✓ Created security group: $SG_ID${NC}"
    echo -e "  Ports: 22 (SSH), 4001 (IPFS), 80 (HTTP), 443 (HTTPS)"
else
    echo -e "${YELLOW}Security group '$SECURITY_GROUP_NAME' already exists: $SG_ID${NC}"
fi
echo ""

# Step 3: Get latest Ubuntu 22.04 AMI
echo -e "${BLUE}Step 3: Finding latest Ubuntu 22.04 LTS AMI...${NC}"
AMI_ID=$(aws ec2 describe-images \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
    --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
    --region "$REGION" \
    --output text)

echo -e "${GREEN}✓ Using AMI: $AMI_ID${NC}"
echo ""

# Step 4: Launch EC2 Instance
echo -e "${BLUE}Step 4: Launching EC2 instance...${NC}"
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$VOLUME_SIZE,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
    --region "$REGION" \
    --query 'Instances[0].InstanceId' \
    --output text)

echo -e "${GREEN}✓ Launched instance: $INSTANCE_ID${NC}"
echo ""

# Wait for instance to be running
echo -e "${BLUE}Waiting for instance to start (this may take 1-2 minutes)...${NC}"
aws ec2 wait instance-running \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION"

# Get public IP
PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

echo -e "${GREEN}✓ Instance is running!${NC}"
echo ""

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Deployment Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Instance Details:${NC}"
echo -e "  Instance ID:   $INSTANCE_ID"
echo -e "  Instance Type: $INSTANCE_TYPE"
echo -e "  Public IP:     $PUBLIC_IP"
echo -e "  Region:        $REGION"
echo -e "  SSH Key:       ~/.ssh/${KEY_NAME}.pem"
echo ""
echo -e "${GREEN}Next Steps:${NC}"
echo -e "  1. Wait 30 seconds for instance initialization to complete"
echo -e "  2. Run the setup script:"
echo -e "     ${YELLOW}./scripts/setup-instance.sh $PUBLIC_IP${NC}"
echo ""
echo -e "  3. SSH to instance manually:"
echo -e "     ${YELLOW}ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@$PUBLIC_IP${NC}"
echo ""
echo -e "${GREEN}Cloudflare DNS:${NC}"
echo -e "  Add an A record pointing to: ${YELLOW}$PUBLIC_IP${NC}"
echo ""

# Save deployment info
cat > deployment-info.txt << EOF
Instance ID: $INSTANCE_ID
Public IP: $PUBLIC_IP
Region: $REGION
SSH Key: ~/.ssh/${KEY_NAME}.pem
Security Group: $SG_ID

SSH Command:
ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@$PUBLIC_IP
EOF

echo -e "${GREEN}✓ Deployment info saved to: deployment-info.txt${NC}"
