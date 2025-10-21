#!/bin/bash
set -e

# Arke IPFS Server - Remote Setup Script
# This script runs ON the EC2 instance to install Docker and start services

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Arke IPFS Server - Instance Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Update system packages
echo -e "${BLUE}Step 1: Updating system packages...${NC}"
sudo apt-get update -qq
echo -e "${GREEN}✓ System updated${NC}"
echo ""

# Install essential tools
echo -e "${BLUE}Step 1.5: Installing essential tools (jq)...${NC}"
sudo apt-get install -y -qq jq
echo -e "${GREEN}✓ Essential tools installed${NC}"
echo ""

# Install Docker
echo -e "${BLUE}Step 2: Installing Docker...${NC}"
if command -v docker &> /dev/null; then
    echo -e "${YELLOW}Docker already installed${NC}"
else
    # Install dependencies
    sudo apt-get install -y -qq \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    # Add Docker's official GPG key
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

    # Set up repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    # Install Docker Engine
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Add ubuntu user to docker group
    sudo usermod -aG docker ubuntu

    echo -e "${GREEN}✓ Docker installed${NC}"
fi
echo ""

# Start Docker service
echo -e "${BLUE}Step 3: Starting Docker service...${NC}"
sudo systemctl enable docker
sudo systemctl start docker
echo -e "${GREEN}✓ Docker service started${NC}"
echo ""

# Create working directory
echo -e "${BLUE}Step 4: Setting up working directory...${NC}"
mkdir -p ~/ipfs-server
cd ~/ipfs-server
echo -e "${GREEN}✓ Working directory created: ~/ipfs-server${NC}"
echo ""

# Create data directories
echo -e "${BLUE}Step 5: Creating data directories...${NC}"
mkdir -p data snapshots backups scripts
echo -e "${GREEN}✓ Data directories created${NC}"
echo ""

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Setup Complete!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Docker version:${NC}"
docker --version
echo ""
echo -e "${YELLOW}Note: You may need to log out and back in for Docker group permissions to take effect.${NC}"
echo -e "${YELLOW}Or run: newgrp docker${NC}"
echo ""
