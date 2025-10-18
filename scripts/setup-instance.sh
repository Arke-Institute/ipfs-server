#!/bin/bash
set -e

# Arke IPFS Server - Instance Setup Orchestrator
# This script runs LOCALLY and sets up the remote EC2 instance

if [ -z "$1" ]; then
    echo "Usage: ./scripts/setup-instance.sh <public-ip>"
    echo "Example: ./scripts/setup-instance.sh 54.123.45.67"
    exit 1
fi

PUBLIC_IP=$1
KEY_FILE="$HOME/.ssh/arke-ipfs-key.pem"
SSH_USER="ubuntu"
REMOTE_DIR="/home/ubuntu/ipfs-server"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Arke IPFS Server - Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "Target: ${YELLOW}$SSH_USER@$PUBLIC_IP${NC}"
echo ""

# Check key file exists
if [ ! -f "$KEY_FILE" ]; then
    echo -e "${YELLOW}Error: SSH key not found at $KEY_FILE${NC}"
    echo -e "${YELLOW}Did you run deploy-ec2.sh first?${NC}"
    exit 1
fi

# Wait for SSH to be ready
echo -e "${BLUE}Waiting for SSH to be ready...${NC}"
max_attempts=30
attempt=0
while ! ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$SSH_USER@$PUBLIC_IP" "echo 'SSH ready'" &> /dev/null; do
    attempt=$((attempt + 1))
    if [ $attempt -ge $max_attempts ]; then
        echo -e "${YELLOW}Error: Could not connect via SSH after $max_attempts attempts${NC}"
        exit 1
    fi
    echo -e "  Attempt $attempt/$max_attempts..."
    sleep 5
done
echo -e "${GREEN}✓ SSH connection established${NC}"
echo ""

# Upload remote setup script
echo -e "${BLUE}Step 1: Uploading setup script...${NC}"
scp -i "$KEY_FILE" -o StrictHostKeyChecking=no \
    scripts/remote-setup.sh "$SSH_USER@$PUBLIC_IP:/tmp/"
echo -e "${GREEN}✓ Setup script uploaded${NC}"
echo ""

# Run remote setup
echo -e "${BLUE}Step 2: Running remote setup (installing Docker)...${NC}"
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" \
    "bash /tmp/remote-setup.sh"
echo ""

# Upload project files
echo -e "${BLUE}Step 3: Uploading project files...${NC}"

# Create list of files to upload
FILES_TO_UPLOAD=(
    "docker-compose.prod.yml"
    "docker-compose.public.yml"
    "docker-compose.nginx.yml"
    "nginx.conf"
    "README.md"
    "CLAUDE.md"
    "API_WALKTHROUGH.md"
    "DISASTER_RECOVERY.md"
)

# Upload all DR scripts and utility scripts
echo -e "  Uploading scripts..."
scp -i "$KEY_FILE" -o StrictHostKeyChecking=no \
    scripts/build-snapshot.sh \
    scripts/export-car.sh \
    scripts/restore-from-car.sh \
    scripts/verify-entity.sh \
    scripts/switch-public-access.sh \
    "$SSH_USER@$PUBLIC_IP:$REMOTE_DIR/scripts/"

# Upload main files
for file in "${FILES_TO_UPLOAD[@]}"; do
    if [ -f "$file" ]; then
        echo -e "  Uploading $file..."
        scp -i "$KEY_FILE" -o StrictHostKeyChecking=no \
            "$file" "$SSH_USER@$PUBLIC_IP:$REMOTE_DIR/"
    fi
done

# Upload most recent CAR backup and metadata if exists
if ls backups/*.car 1> /dev/null 2>&1; then
    LATEST_CAR=$(ls -t backups/*.car | head -1)
    echo -e "  Uploading backup: $LATEST_CAR..."
    scp -i "$KEY_FILE" -o StrictHostKeyChecking=no \
        "$LATEST_CAR" "$SSH_USER@$PUBLIC_IP:$REMOTE_DIR/backups/"

    # Upload metadata JSON if it exists
    METADATA_JSON="${LATEST_CAR%.car}.json"
    if [ -f "$METADATA_JSON" ]; then
        echo -e "  Uploading metadata: $METADATA_JSON..."
        scp -i "$KEY_FILE" -o StrictHostKeyChecking=no \
            "$METADATA_JSON" "$SSH_USER@$PUBLIC_IP:$REMOTE_DIR/backups/"
    fi
fi

echo -e "${GREEN}✓ Project files uploaded${NC}"
echo ""

# Make scripts executable
echo -e "${BLUE}Step 4: Making scripts executable...${NC}"
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" \
    "chmod +x $REMOTE_DIR/scripts/*.sh"
echo -e "${GREEN}✓ Scripts are executable${NC}"
echo ""

# Start services
echo -e "${BLUE}Step 5: Starting IPFS services...${NC}"
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" << 'ENDSSH'
cd ~/ipfs-server
# Use newgrp to get docker group permissions without logout
newgrp docker << 'ENDNEWGRP'
docker compose -f docker-compose.prod.yml up -d
sleep 5
docker compose -f docker-compose.prod.yml ps
ENDNEWGRP
ENDSSH
echo -e "${GREEN}✓ Services started${NC}"
echo ""

# Check if we should restore from backup
if ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" \
    "ls $REMOTE_DIR/backups/*.car 1> /dev/null 2>&1"; then
    echo -e "${BLUE}========================================${NC}"
    echo -e "${YELLOW}Backup file found on instance!${NC}"
    echo ""
    echo -e "Would you like to restore from backup? (y/n)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Restoring from backup...${NC}"
        CAR_FILE=$(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" \
            "ls -t $REMOTE_DIR/backups/*.car | head -1")
        ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no "$SSH_USER@$PUBLIC_IP" \
            "cd $REMOTE_DIR && CONTAINER_NAME=ipfs-node-prod ./scripts/restore-from-car.sh $CAR_FILE"
        echo -e "${GREEN}✓ Backup restored${NC}"
        echo ""
    fi
fi

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Your IPFS server is now running on:${NC}"
echo -e "  Public IP: ${YELLOW}$PUBLIC_IP${NC}"
echo ""
echo -e "${GREEN}Useful commands:${NC}"
echo -e "  SSH to instance:"
echo -e "    ${YELLOW}ssh -i $KEY_FILE $SSH_USER@$PUBLIC_IP${NC}"
echo ""
echo -e "  Check service status:"
echo -e "    ${YELLOW}docker compose -f docker-compose.prod.yml ps${NC}"
echo ""
echo -e "  View logs:"
echo -e "    ${YELLOW}docker compose -f docker-compose.prod.yml logs -f${NC}"
echo ""
echo -e "  Test IPFS API:"
echo -e "    ${YELLOW}curl -X POST http://localhost:5001/api/v0/version${NC}"
echo ""
echo -e "${GREEN}Next steps:${NC}"
echo -e "  1. Add DNS A record in Cloudflare pointing to: ${YELLOW}$PUBLIC_IP${NC}"
echo -e "  2. Deploy your API service to this instance"
echo -e "  3. Configure API service to connect to: ${YELLOW}http://localhost:5001${NC}"
echo ""
