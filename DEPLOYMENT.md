# EC2 Deployment Guide

This guide covers deploying the Arke IPFS Server to AWS EC2 with automated scripts.

## Prerequisites

1. **AWS Account** with appropriate permissions
2. **AWS CLI** installed and configured (`aws configure`)
3. **Cloudflare Account** with a domain (optional, for DNS)
4. **Local backup** (optional): Latest CAR file in `backups/` directory

## Quick Start

Deploy the entire infrastructure and application with two commands:

```bash
# 1. Provision AWS resources (EC2 instance, security group, SSH key)
./scripts/deploy-ec2.sh

# 2. Set up the instance (Docker, upload files, start services)
./scripts/setup-instance.sh <public-ip>
```

The scripts will output the public IP address - save this for DNS configuration.

## Detailed Deployment Steps

### Step 1: Provision AWS Infrastructure

Run the deployment script:

```bash
./scripts/deploy-ec2.sh
```

This script will:
- ✅ Create SSH key pair (`arke-ipfs-key`) and save to `~/.ssh/`
- ✅ Create security group (`arke-ipfs-sg`) with ports: 22, 4001, 80, 443
- ✅ Launch t3.small EC2 instance with 30GB gp3 EBS volume
- ✅ Wait for instance to start
- ✅ Display instance details and next steps
- ✅ Save deployment info to `deployment-info.txt`

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
  1. Wait 30 seconds for instance initialization to complete
  2. Run the setup script:
     ./scripts/setup-instance.sh 54.123.45.67
```

### Step 2: Set Up the Instance

After waiting ~30 seconds for the instance to fully initialize, run:

```bash
./scripts/setup-instance.sh 54.123.45.67  # Use your actual public IP
```

This script will:
- ✅ Wait for SSH to be ready
- ✅ Install Docker and Docker Compose
- ✅ Upload project files (docker-compose.prod.yml, scripts, docs)
- ✅ Upload latest CAR backup if available
- ✅ Make scripts executable
- ✅ Start IPFS services via Docker Compose
- ✅ Prompt to restore from backup (if CAR file present)

**Note**: If you have a backup CAR file in `backups/`, the script will automatically upload it and offer to restore.

### Step 3: Configure Cloudflare DNS

Add an A record in Cloudflare to point your domain to the EC2 instance:

1. **Log in to Cloudflare Dashboard**: https://dash.cloudflare.com/
2. **Select your domain** from the list
3. **Navigate to DNS** → DNS Records
4. **Add a new A record**:
   - **Type**: A
   - **Name**: `ipfs` (or your preferred subdomain, or `@` for root domain)
   - **IPv4 address**: `54.123.45.67` (your EC2 public IP)
   - **TTL**: Auto (or 300 seconds for faster updates)
   - **Proxy status**:
     - ☁️ **Proxied** (recommended) - Cloudflare CDN, DDoS protection, free SSL
     - 🌐 **DNS only** - Direct connection to EC2 (if you need direct IPFS access)
5. **Click Save**

**Result**:
- Proxied: `https://ipfs.yourdomain.com` → Cloudflare → EC2
- DNS only: `http://ipfs.yourdomain.com` → EC2 directly

**DNS Propagation**: Changes typically take 1-5 minutes with low TTL.

### Step 4: Verify Deployment

SSH into your instance:

```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@54.123.45.67
```

Check services are running:

```bash
cd ~/ipfs-server
docker compose -f docker-compose.prod.yml ps
```

You should see:

```
NAME                IMAGE               STATUS
ipfs-server-ipfs-1  ipfs/kubo:latest   Up 2 minutes (healthy)
```

Test IPFS API:

```bash
curl -X POST http://localhost:5001/api/v0/version
```

View logs:

```bash
docker compose -f docker-compose.prod.yml logs -f
```

## Port Configuration

The production deployment (`docker-compose.prod.yml`) exposes:

- **Port 4001**: IPFS Swarm (public, for IPFS network connectivity)
- **Port 5001**: HTTP RPC API (localhost only, for API service)
- **Port 8080**: HTTP Gateway (localhost only, for content serving)

Security group additionally opens:
- **Port 22**: SSH (for management)
- **Port 80/443**: HTTP/HTTPS (for future API service)

## Instance Management

### SSH Access

```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
```

### View Service Status

```bash
docker compose -f docker-compose.prod.yml ps
```

### View Logs

```bash
# Follow logs (Ctrl+C to exit)
docker compose -f docker-compose.prod.yml logs -f

# View last 100 lines
docker compose -f docker-compose.prod.yml logs --tail=100
```

### Restart Services

```bash
docker compose -f docker-compose.prod.yml restart
```

### Stop Services

```bash
docker compose -f docker-compose.prod.yml down
```

### Start Services

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Update Code/Configuration

From your local machine, upload new files and restart:

```bash
# Upload specific file
scp -i ~/.ssh/arke-ipfs-key.pem \
    docker-compose.prod.yml \
    ubuntu@<public-ip>:~/ipfs-server/

# SSH in and restart
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
cd ~/ipfs-server
docker compose -f docker-compose.prod.yml up -d
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
./scripts/restore-from-car.sh backups/arke-{seq}-{timestamp}.car
```

Or upload and restore from local:

```bash
# Upload CAR file
scp -i ~/.ssh/arke-ipfs-key.pem \
    ./backups/arke-*.car \
    ubuntu@<public-ip>:~/ipfs-server/backups/

# SSH and restore
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
cd ~/ipfs-server
./scripts/restore-from-car.sh backups/arke-{seq}-{timestamp}.car
```

## Monitoring and Maintenance

### Check IPFS Repo Size

```bash
curl -X POST http://localhost:5001/api/v0/repo/stat | jq .
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

### View System Resources

```bash
htop  # Interactive (install with: sudo apt install htop)
# or
top
```

## Scaling and Upgrades

### Upgrade Instance Type

If you need more resources:

1. Stop services:
   ```bash
   docker compose -f docker-compose.prod.yml down
   ```

2. In AWS Console or CLI, stop instance and change instance type:
   ```bash
   aws ec2 stop-instances --instance-ids <instance-id>
   aws ec2 modify-instance-attribute \
       --instance-id <instance-id> \
       --instance-type t3.medium
   aws ec2 start-instances --instance-ids <instance-id>
   ```

3. Start services:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
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

### IPFS Not Starting

```bash
# Check logs
docker compose -f docker-compose.prod.yml logs ipfs

# Check if container is running
docker ps -a

# Try restart
docker compose -f docker-compose.prod.yml restart
```

### Out of Disk Space

```bash
# Check usage
df -h

# Clean up Docker
docker system prune -a

# Consider expanding EBS volume (see Scaling section)
```

### Can't Access via Domain

- Verify DNS A record points to correct IP
- Check DNS propagation: `dig ipfs.yourdomain.com`
- If using Cloudflare proxy, ensure SSL/TLS mode is correct
- Test direct IP access first to isolate DNS issues

## Security Hardening

The current setup is "open for now" as requested, but consider these hardening steps for production:

1. **Restrict SSH access**:
   ```bash
   # Modify security group to allow SSH only from your IP
   aws ec2 authorize-security-group-ingress \
       --group-id <sg-id> \
       --protocol tcp --port 22 \
       --cidr <your-ip>/32
   ```

2. **Set up firewall (ufw)**:
   ```bash
   sudo ufw allow 22/tcp
   sudo ufw allow 4001/tcp
   sudo ufw enable
   ```

3. **Enable automatic security updates**:
   ```bash
   sudo apt install unattended-upgrades
   sudo dpkg-reconfigure -plow unattended-upgrades
   ```

4. **Set up CloudWatch monitoring** for logs and metrics

5. **Enable EBS encryption** for data at rest

6. **Use AWS Secrets Manager** for sensitive configuration

## Cost Estimation

Current configuration (us-east-1):
- **t3.small instance**: ~$15/month
- **30GB gp3 EBS**: ~$2.40/month
- **Data transfer**: Variable (typically minimal for API usage)

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

For IPFS-specific issues, see:
- `README.md` - Basic operations
- `API_WALKTHROUGH.md` - API integration guide
- `DISASTER_RECOVERY.md` - DR procedures
- `CLAUDE.md` - Project architecture

For AWS issues, consult AWS documentation or your AWS support plan.
