# EC2 Deployment Guide

This guide covers deploying the Arke IPFS Server to AWS EC2 with automated scripts.

## Prerequisites

1. **AWS Account** with appropriate permissions
2. **AWS CLI** installed and configured (`aws configure`)
3. **Cloudflare Account** with domain `arke.institute` configured
4. **Local backup** (optional): Latest CAR file in `backups/` directory

## Architecture Overview

The deployment includes three services running in Docker containers:

1. **IPFS/Kubo** (ipfs-node-prod) - Storage layer on ports 5001/8080
2. **API Service** (ipfs-api) - FastAPI REST API on port 3000
3. **Nginx** (ipfs-nginx) - Reverse proxy with rate limiting on ports 80/443

All services communicate via Docker bridge network and are accessible publicly via Cloudflare DNS.

## Quick Start

Deploy the entire infrastructure with two commands:

```bash
# 1. Provision AWS resources (EC2 instance, security group, SSH key)
./scripts/deploy-ec2.sh

# 2. Set up the instance (Docker, upload files, start all services)
./scripts/setup-instance.sh <public-ip> [--no-restore]
```

Add `--no-restore` flag to skip backup restoration prompt (useful for fresh deployments).

## Detailed Deployment Steps

### Step 1: Provision AWS Infrastructure

Run the deployment script:

```bash
./scripts/deploy-ec2.sh
```

This script will:
- ✅ Create SSH key pair (`arke-ipfs-key`) and save to `~/.ssh/`
- ✅ Create security group (`arke-ipfs-sg`) with ports: 22, 80, 443, 4001
- ✅ Launch t3.small EC2 instance with 30GB gp3 EBS volume
- ✅ Wait for instance to start
- ✅ Display instance details and next steps
- ✅ Save deployment info to `deployment-info.txt` (gitignored)

**Output example:**
```
========================================
  Deployment Complete!
========================================

Instance Details:
  Instance ID:   i-0123456789abcdef0
  Instance Type: t3.small
  Public IP:     54.123.45.67
  Region:        us-east-1
  SSH Key:       ~/.ssh/arke-ipfs-key.pem

Next Steps:
  1. Wait 30 seconds for instance initialization
  2. Run: ./scripts/setup-instance.sh 54.123.45.67
```

### Step 2: Set Up the Instance

After waiting ~30 seconds, run:

```bash
# Fresh deployment (skips backup restore)
./scripts/setup-instance.sh 54.123.45.67 --no-restore

# Or with backup restoration
./scripts/setup-instance.sh 54.123.45.67
```

This script will:
- ✅ Wait for SSH to be ready
- ✅ Install Docker and essential tools (jq)
- ✅ Upload project files (docker-compose.nginx.yml, nginx.conf, api/, scripts/, docs)
- ✅ Upload latest CAR backup if available
- ✅ Make scripts executable
- ✅ Start all 3 services via `docker-compose.nginx.yml`
- ✅ Optionally restore from backup (if not using `--no-restore`)

**Services deployed:**
- IPFS/Kubo node (port 5001/8080)
- API Service (port 3000)
- Nginx reverse proxy (ports 80/443)

### Step 3: Configure Cloudflare DNS

Add two A records in Cloudflare:

1. **Log in to Cloudflare Dashboard**: https://dash.cloudflare.com/
2. **Select arke.institute** domain
3. **Navigate to DNS** → DNS Records
4. **Add A records**:

   **Record 1 - API/RPC endpoint:**
   - **Type**: A
   - **Name**: `ipfs-api`
   - **IPv4 address**: `54.123.45.67` (your EC2 public IP)
   - **TTL**: Auto
   - **Proxy status**: ☁️ **Proxied** (for SSL and DDoS protection)

   **Record 2 - Gateway endpoint:**
   - **Type**: A
   - **Name**: `ipfs`
   - **IPv4 address**: `54.123.45.67` (your EC2 public IP)
   - **TTL**: Auto
   - **Proxy status**: ☁️ **Proxied**

5. **Set SSL/TLS mode** to **Flexible**:
   - Go to **SSL/TLS** → **Overview**
   - Select **Flexible** (Visitor → Cloudflare uses HTTPS, Cloudflare → Server uses HTTP)

**Result:**
- API/RPC: `https://ipfs-api.arke.institute` → Nginx → IPFS/API Service
- Gateway: `https://ipfs.arke.institute` → Nginx → IPFS Gateway

**DNS Propagation**: Changes typically take 1-5 minutes.

### Step 4: Verify Deployment

Test endpoints:

```bash
# Health check
curl https://ipfs-api.arke.institute/health
# → {"status":"healthy"}

# IPFS version
curl -X POST https://ipfs-api.arke.institute/api/v0/version
# → {"Version":"0.38.1",...}

# Events endpoint
curl https://ipfs-api.arke.institute/events
# → {"items":[],...}

# Index pointer
curl https://ipfs-api.arke.institute/index-pointer
# → {"schema":"arke/index-pointer@v2",...}
```

SSH into instance:

```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@54.123.45.67
```

Check services:

```bash
cd ~/ipfs-server
docker compose -f docker-compose.nginx.yml ps
```

Expected output:
```
NAME             IMAGE                  STATUS
ipfs-api         ipfs-server-ipfs-api   Up (healthy)
ipfs-nginx       nginx:alpine           Up
ipfs-node-prod   ipfs/kubo:latest       Up (healthy)
```

## Port Configuration

**External (via Cloudflare):**
- `443/tcp` → Nginx (HTTPS traffic proxied by Cloudflare)

**Nginx Routing:**
- `/health`, `/events`, `/snapshot`, `/index-pointer` → API Service (port 3000)
- `/api/v0/*` → IPFS RPC API (port 5001)
- All other paths → IPFS Gateway (port 8080)

**Security Group:**
- `22/tcp` - SSH (for management)
- `80/tcp` - HTTP (Cloudflare origin)
- `443/tcp` - HTTPS (for future direct SSL)
- `4001/tcp` - IPFS Swarm (P2P network)

## Instance Management

### SSH Access

```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
```

### View Service Status

```bash
docker compose -f docker-compose.nginx.yml ps
```

### View Logs

```bash
# All services
docker compose -f docker-compose.nginx.yml logs -f

# Specific service
docker logs ipfs-api -f
docker logs ipfs-node-prod -f
docker logs ipfs-nginx -f

# Last 100 lines
docker compose -f docker-compose.nginx.yml logs --tail=100
```

### Restart Services

```bash
# All services
docker compose -f docker-compose.nginx.yml restart

# Specific service
docker restart ipfs-api
```

### Stop Services

```bash
docker compose -f docker-compose.nginx.yml down
```

### Start Services

```bash
docker compose -f docker-compose.nginx.yml up -d
```

### Update Code/Configuration

From your local machine:

```bash
# Upload updated files
scp -i ~/.ssh/arke-ipfs-key.pem \
    -r api/ nginx.conf docker-compose.nginx.yml \
    ubuntu@<public-ip>:~/ipfs-server/

# SSH in and restart
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
cd ~/ipfs-server
docker compose -f docker-compose.nginx.yml up -d --build
```

## Backup and Restore

### Create Backup

SSH into instance and run:

```bash
cd ~/ipfs-server
./scripts/build-snapshot.sh
./scripts/export-car.sh
```

This creates a CAR file in `backups/arke-{seq}-{timestamp}.car`.

### Download Backup

From your local machine:

```bash
scp -i ~/.ssh/arke-ipfs-key.pem \
    ubuntu@<public-ip>:~/ipfs-server/backups/arke-*.car \
    ./backups/
```

### Restore from Backup

On the instance:

```bash
cd ~/ipfs-server
CONTAINER_NAME=ipfs-node-prod ./scripts/restore-from-car.sh backups/arke-{seq}-{timestamp}.car
```

Or use automated deployment with restoration:

```bash
# From local machine - deploys and asks to restore
./scripts/setup-instance.sh 54.123.45.67
```

## Monitoring and Maintenance

### Check IPFS Repo Size

```bash
curl -X POST http://localhost:5001/api/v0/repo/stat | jq .
```

### Check API Service Health

```bash
curl https://ipfs-api.arke.institute/health
curl https://ipfs-api.arke.institute/index-pointer
```

### Check Connected Peers

```bash
curl -X POST http://localhost:5001/api/v0/swarm/peers | jq '. | length'
```

### Check Disk Usage

```bash
df -h
```

### Check Container Resource Usage

```bash
docker stats
```

### View Nginx Access Logs

```bash
docker exec ipfs-nginx tail -f /var/log/nginx/access.log
```

## Scaling and Upgrades

### Upgrade Instance Type

If you need more resources:

1. Stop services:
   ```bash
   docker compose -f docker-compose.nginx.yml down
   ```

2. Stop instance and change type:
   ```bash
   aws ec2 stop-instances --instance-ids <instance-id>
   aws ec2 modify-instance-attribute \
       --instance-id <instance-id> \
       --instance-type t3.medium
   aws ec2 start-instances --instance-ids <instance-id>
   ```

3. Start services:
   ```bash
   docker compose -f docker-compose.nginx.yml up -d
   ```

### Expand EBS Volume

If you need more storage:

1. In AWS Console or CLI, modify EBS volume size
2. Extend filesystem on instance:
   ```bash
   sudo growpart /dev/xvda 1
   sudo resize2fs /dev/xvda1
   ```

## Troubleshooting

### Can't Connect via SSH

- Check security group allows port 22 from your IP
- Verify you're using correct key: `~/.ssh/arke-ipfs-key.pem`
- Verify key permissions: `chmod 400 ~/.ssh/arke-ipfs-key.pem`
- Check instance is running: `aws ec2 describe-instances --instance-ids <id>`

### Services Not Starting

```bash
# Check logs
docker compose -f docker-compose.nginx.yml logs

# Check if containers are running
docker ps -a

# Rebuild and restart
docker compose -f docker-compose.nginx.yml up -d --build
```

### API Service Errors

```bash
# Check API logs
docker logs ipfs-api --tail 50

# Restart API service
docker restart ipfs-api
```

### Can't Access via Domain

- Verify DNS A records point to correct IP
- Check DNS propagation: `dig ipfs-api.arke.institute`
- Ensure Cloudflare SSL/TLS mode is "Flexible"
- Check Cloudflare proxy status is enabled (orange cloud)
- Test direct IP access to isolate DNS issues

### Nginx Routing Issues

```bash
# Check nginx config
docker exec ipfs-nginx cat /etc/nginx/nginx.conf

# Test nginx config
docker exec ipfs-nginx nginx -t

# Restart nginx
docker restart ipfs-nginx
```

### Out of Disk Space

```bash
# Check usage
df -h

# Clean up Docker
docker system prune -a

# Check IPFS repo size
curl -X POST http://localhost:5001/api/v0/repo/stat

# Consider expanding EBS volume (see Scaling section)
```

## Security Notes

Current setup uses:
- **Rate limiting**: 1000 req/s for API, 500 req/s for Gateway (configured for MVP testing)
- **Security headers**: X-Frame-Options, X-Content-Type-Options, X-XSS-Protection
- **Connection limits**: 200 concurrent for API, 100 for Gateway
- **Cloudflare DDoS protection**: Via proxied DNS

For production hardening:
1. Reduce rate limits to production values
2. Restrict SSH access to specific IPs
3. Enable automatic security updates
4. Set up CloudWatch monitoring
5. Enable EBS encryption

## Cost Estimation

Current configuration (us-east-1):
- **t3.small instance**: ~$15/month
- **30GB gp3 EBS**: ~$2.40/month
- **Data transfer**: Variable (Cloudflare bandwidth is free)

**Total**: ~$17-20/month

## Teardown

To completely remove all AWS resources:

```bash
# Get instance ID from deployment-info.txt or:
INSTANCE_ID=$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=arke-ipfs-server" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text)

# Terminate instance (also deletes EBS volume)
aws ec2 terminate-instances --instance-ids $INSTANCE_ID

# Delete security group (wait for instance to terminate first)
aws ec2 delete-security-group --group-name arke-ipfs-sg

# Delete key pair
aws ec2 delete-key-pair --key-name arke-ipfs-key
rm ~/.ssh/arke-ipfs-key.pem

# Remove deployment info
rm deployment-info.txt
```

## Support

For documentation:
- `README.md` - Basic operations
- `API_WALKTHROUGH.md` - API integration guide
- `DISASTER_RECOVERY.md` - DR procedures
- `CLAUDE.md` - Project architecture

For issues, consult AWS or Cloudflare documentation.
