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

Deploy the entire infrastructure:

```bash
# 1. Set up AWS backup infrastructure (one-time setup)
./scripts/create-s3-backup-bucket.sh
./scripts/create-iam-role.sh
./scripts/configure-ebs-snapshots.sh

# 2. Provision AWS resources (EC2 instance, security group, SSH key)
./scripts/deploy-ec2.sh

# 3. Set up the instance (Docker, upload files, start all services)
./scripts/setup-instance.sh <public-ip> [--no-restore] [--upload-backup]
```

**Flags:**
- `--no-restore`: Skip backup restoration prompt (useful for fresh deployments)
- `--upload-backup`: Upload local CAR file to instance during deployment

**Note**: Step 1 (backup infrastructure) only needs to be run once. Subsequent deployments skip this step.

## Detailed Deployment Steps

### Step 0: Configure Backup Infrastructure (One-Time Setup)

Before deploying your first instance, set up the backup infrastructure:

```bash
# Create S3 bucket for offsite backups
./scripts/create-s3-backup-bucket.sh

# Create IAM role for EC2 S3 access
./scripts/create-iam-role.sh

# Configure EBS automated snapshots
./scripts/configure-ebs-snapshots.sh
```

**What this does:**
- Creates S3 bucket: `arke-ipfs-backups-{account-id}` with lifecycle policies
- Creates IAM role: `arke-ipfs-ec2-backup-role` with S3 upload permissions
- Configures DLM for daily EBS snapshots at 3:00 AM UTC

**When to run:**
- First-time deployment: Run all three scripts
- Subsequent deployments: Skip (infrastructure already exists)
- Multiple instances: Share same S3 bucket and IAM role

See the **Automated Backup Strategy** section below for detailed backup documentation.

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

# Or upload local CAR backup during deployment
./scripts/setup-instance.sh 54.123.45.67 --upload-backup
```

This script will:
- ✅ Wait for SSH to be ready
- ✅ Install Docker and essential tools (jq)
- ✅ Upload project files (docker-compose.nginx.yml, nginx.conf, api/, scripts/, docs)
- ✅ Upload latest CAR backup if `--upload-backup` flag is used
- ✅ Make scripts executable
- ✅ Start all 3 services via `docker-compose.nginx.yml`
- ✅ Optionally restore from backup (if not using `--no-restore`)

**Services deployed:**
- IPFS/Kubo node (port 5001/8080)
- API Service (port 3000)
- Nginx reverse proxy (ports 80/443)

**Next step**: Configure automated daily backups (see Step 3a below)

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

### Step 3a: Configure Automated Daily Backups

SSH into the instance and set up the cron job:

```bash
# SSH to instance
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@54.123.45.67

# Configure cron job for daily backups at 2 AM UTC
(crontab -l 2>/dev/null; echo "0 2 * * * /home/ubuntu/ipfs-server/scripts/daily-car-export.sh >> /var/log/arke-backup.log 2>&1") | crontab -

# Verify cron is configured
crontab -l

# Exit SSH
exit
```

**What this does:**
- Runs daily at 2:00 AM UTC
- Builds IPFS snapshot index
- Exports to CAR file
- Uploads to S3 (if AWS CLI configured)
- Cleans up CAR files older than 3 days
- Logs output to `/var/log/arke-backup.log`

**Verify backup job works:**
```bash
# Wait for 2 AM UTC, then check logs
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@54.123.45.67
tail -50 /var/log/arke-backup.log
```

See **Automated Backup Strategy** section for detailed backup documentation.

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

## Automated Backup Strategy

The deployment includes a comprehensive 4-layer backup strategy for disaster recovery:

### Backup Architecture

1. **Hourly Snapshots** (in-IPFS, scheduler-based)
   - Runs every 60 minutes via APScheduler in ipfs-api container
   - Creates snapshot indexes stored in IPFS as dag-json
   - Enables point-in-time recovery within the last hour
   - No additional cost (stored in IPFS)

2. **Daily CAR Exports** (local, 3-day retention)
   - Runs daily at 2:00 AM UTC via cron
   - Exports complete snapshot to portable CAR file
   - Stored in `/home/ubuntu/ipfs-server/backups/`
   - Auto-deletes files older than 3 days
   - Typical size: 15-50 MB

3. **Daily S3 Backups** (offsite, 90-day lifecycle)
   - Automatically uploads CAR files to S3 after export
   - Bucket: `arke-ipfs-backups-{account-id}`
   - Lifecycle: Days 0-7 Standard → Days 7-90 Glacier → Day 90+ Delete
   - Encrypted at rest (AES256)
   - Cost: ~$3/month

4. **Daily EBS Snapshots** (infrastructure, 7-day retention)
   - Runs daily at 3:00 AM UTC via AWS Data Lifecycle Manager
   - Infrastructure-level backup of entire EBS volume
   - Retains 7 snapshots (rolling window)
   - Tagged with `SnapshotType=DLM-Automated`
   - Cost: ~$10.50/month

### Initial AWS Infrastructure Setup

Before deployment, configure AWS backup infrastructure:

#### 1. Create S3 Backup Bucket

```bash
./scripts/create-s3-backup-bucket.sh
```

This creates:
- S3 bucket with versioning enabled
- AES256 encryption
- Lifecycle policy (Standard → Glacier → Delete)
- Public access blocked
- Bucket name: `arke-ipfs-backups-{account-id}`

#### 2. Create IAM Role for S3 Access

```bash
./scripts/create-iam-role.sh
```

This creates:
- IAM role: `arke-ipfs-ec2-backup-role`
- Instance profile for EC2 attachment
- Policy allowing S3 PutObject/ListBucket to backup bucket only
- Follows principle of least privilege

#### 3. Configure EBS Automated Snapshots

```bash
./scripts/configure-ebs-snapshots.sh
```

This creates:
- AWS Data Lifecycle Manager (DLM) policy
- Targets volumes tagged with `Name=arke-ipfs-server`
- Daily snapshots at 3:00 AM UTC
- Retains 7 snapshots
- Auto-tags snapshots with `SnapshotType=DLM-Automated`

**Note**: Run these scripts once during initial setup. The `deploy-ec2.sh` script automatically attaches the IAM role to new instances.

#### 4. Attach IAM Role to Existing Instance

If you have an existing instance without the IAM role:

```bash
./scripts/attach-iam-to-instance.sh <instance-id>
```

Example:
```bash
./scripts/attach-iam-to-instance.sh i-0443444abcd3ed689
```

### Automated Daily Backups

After deployment, configure the cron job on the EC2 instance:

```bash
# SSH into instance
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>

# Add cron job (runs daily at 2 AM UTC)
(crontab -l 2>/dev/null; echo "0 2 * * * /home/ubuntu/ipfs-server/scripts/daily-car-export.sh >> /var/log/arke-backup.log 2>&1") | crontab -
```

The daily backup pipeline:
1. Builds snapshot index from current MFS state
2. Exports snapshot to CAR file
3. Cleans up CAR files older than 3 days
4. Uploads latest CAR to S3 (if AWS CLI configured)

**Verify cron is configured:**
```bash
crontab -l
```

**Check backup logs:**
```bash
tail -f /var/log/arke-backup.log
```

### Manual Backup Procedures

#### Create On-Demand Backup

```bash
# SSH into instance
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
cd ~/ipfs-server

# Run backup pipeline manually
./scripts/daily-car-export.sh
```

#### Upload Specific CAR File to S3

```bash
./scripts/upload-to-s3.sh backups/arke-5-20251016-120000.car
```

### Restore Procedures

#### Restore from Local CAR File

On the EC2 instance:

```bash
cd ~/ipfs-server
CONTAINER_NAME=ipfs-node-prod ./scripts/restore-from-car.sh backups/arke-{seq}-{timestamp}.car
```

#### Restore from S3 Backup

```bash
# Download from S3
aws s3 cp s3://arke-ipfs-backups-{account-id}/daily/arke-{seq}-{timestamp}.car ./backups/

# Restore
CONTAINER_NAME=ipfs-node-prod ./scripts/restore-from-car.sh backups/arke-{seq}-{timestamp}.car
```

#### Restore from EBS Snapshot

For complete infrastructure failure:

1. **Create volume from snapshot:**
   ```bash
   # List available snapshots
   aws ec2 describe-snapshots \
       --owner-ids self \
       --filters "Name=tag:SnapshotType,Values=DLM-Automated" \
       --query 'Snapshots | sort_by(@, &StartTime) | [-1].[SnapshotId,StartTime,Description]' \
       --output table

   # Create volume from snapshot
   aws ec2 create-volume \
       --snapshot-id snap-0123456789abcdef0 \
       --availability-zone us-east-1a \
       --volume-type gp3 \
       --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=arke-ipfs-restored}]'
   ```

2. **Launch new instance with restored volume** or attach to existing instance

3. **Verify data integrity:**
   ```bash
   docker compose -f docker-compose.nginx.yml up -d
   curl http://localhost:3000/health
   curl http://localhost:3000/index-pointer
   ```

#### Complete Disaster Recovery

For total infrastructure loss (tested procedure):

1. **Deploy fresh infrastructure:**
   ```bash
   ./scripts/deploy-ec2.sh
   ```

2. **Download latest backup from S3:**
   ```bash
   # On local machine
   aws s3 ls s3://arke-ipfs-backups-{account-id}/daily/ --recursive | sort | tail -1
   aws s3 cp s3://arke-ipfs-backups-{account-id}/daily/arke-{seq}-{timestamp}.car ./backups/
   ```

3. **Deploy with restoration:**
   ```bash
   ./scripts/setup-instance.sh <public-ip>
   # When prompted, choose to restore from backup
   ```

4. **Verify restoration:**
   ```bash
   ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
   cd ~/ipfs-server
   ./scripts/verify-entity.sh <known-PI>
   ```

### Backup Monitoring

#### Check Backup Status

```bash
# View recent backups
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
ls -lh ~/ipfs-server/backups/

# Check S3 backups
aws s3 ls s3://arke-ipfs-backups-{account-id}/daily/ --recursive --human-readable

# View EBS snapshots
aws ec2 describe-snapshots \
    --owner-ids self \
    --filters "Name=tag:SnapshotType,Values=DLM-Automated" \
    --query 'Snapshots[].[SnapshotId,StartTime,State,VolumeSize]' \
    --output table
```

#### Verify Backup Integrity

```bash
# Check last backup log
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
tail -50 /var/log/arke-backup.log

# Verify snapshot CID is accessible
cd ~/ipfs-server
SNAPSHOT_CID=$(cat snapshots/latest.json | grep -o 'baguqee[a-z0-9]*')
docker exec ipfs-node-prod ipfs dag get $SNAPSHOT_CID | head -20

# Test restore (dry run)
CONTAINER_NAME=ipfs-node-prod ./scripts/restore-from-car.sh backups/arke-*.car --verify-only
```

#### Set Up Backup Alerts (Optional)

For production monitoring, configure CloudWatch alarms:

```bash
# Create SNS topic for alerts
aws sns create-topic --name arke-backup-alerts
aws sns subscribe --topic-arn arn:aws:sns:us-east-1:{account}:arke-backup-alerts \
    --protocol email --notification-endpoint your-email@example.com

# Create CloudWatch alarm for failed DLM snapshots
aws cloudwatch put-metric-alarm \
    --alarm-name arke-ebs-snapshot-failures \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 1 \
    --metric-name DLMFailedSnapshots \
    --namespace AWS/DLM \
    --period 86400 \
    --statistic Sum \
    --threshold 0 \
    --alarm-actions arn:aws:sns:us-east-1:{account}:arke-backup-alerts
```

### Backup Cost Breakdown

**Monthly backup costs (us-east-1):**

1. **EBS Snapshots** (7 snapshots × 30GB × 5% incremental):
   - Storage: 10.5 GB average
   - Cost: ~$0.05/GB/month = **$0.53/month**
   - (First snapshot is full 30GB, subsequent are incremental ~1-2GB each)

2. **S3 Backups** (90-day lifecycle):
   - Standard storage (0-7 days): ~7 × 20MB = 140 MB
   - Glacier storage (7-90 days): ~83 × 20MB = 1.66 GB
   - Standard cost: 140 MB × $0.023/GB = **$0.003/month**
   - Glacier cost: 1.66 GB × $0.004/GB = **$0.007/month**
   - PUT requests: 30/month × $0.005/1000 = **$0.0002/month**
   - Total S3: **$0.01/month** (negligible)

3. **Data Transfer**:
   - S3 uploads: Free (within region)
   - CAR file downloads (if needed): $0.09/GB (only when restoring)

**Total backup infrastructure: ~$0.54/month**

**Note**: These are the incremental backup costs. See main cost section for complete infrastructure costs.

### Recovery Objectives

- **RTO (Recovery Time Objective)**: 5-30 minutes
  - CAR restore: ~5 minutes (local file)
  - S3 restore: ~10 minutes (download + restore)
  - EBS restore: ~20-30 minutes (volume creation + instance launch)

- **RPO (Recovery Point Objective)**: 1-24 hours
  - Hourly snapshots: up to 1 hour data loss
  - Daily CAR exports: up to 24 hours data loss
  - Daily EBS snapshots: up to 24 hours data loss

### Backup Best Practices

1. **Test restores regularly** (monthly recommended):
   ```bash
   # Spin up test instance and restore latest backup
   ./scripts/deploy-ec2.sh  # Creates new instance
   ./scripts/setup-instance.sh <test-ip>  # Restore from backup
   ```

2. **Keep local backups** for critical events:
   ```bash
   # Download important backups locally
   scp -i ~/.ssh/arke-ipfs-key.pem \
       ubuntu@<public-ip>:~/ipfs-server/backups/arke-{seq}-{timestamp}.car \
       ./backups/critical/
   ```

3. **Monitor backup logs** for failures:
   ```bash
   # Check for errors in last 7 days
   ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
   grep -i error /var/log/arke-backup.log | tail -20
   ```

4. **Verify IAM permissions** are working:
   ```bash
   # Test S3 access from instance
   ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<public-ip>
   aws s3 ls s3://arke-ipfs-backups-{account-id}/daily/
   ```

5. **Document critical snapshot CIDs** for disaster recovery:
   ```bash
   # Save snapshot metadata for key milestones
   cat snapshots/latest.json | tee backups/milestone-2025-01-15.json
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

### Infrastructure Costs
- **t3.small instance**: ~$15/month
- **30GB gp3 EBS**: ~$2.40/month
- **Data transfer**: Variable (Cloudflare bandwidth is free)

**Infrastructure subtotal**: ~$17-18/month

### Backup Costs
- **EBS snapshots** (7 × 30GB incremental): ~$0.53/month
- **S3 storage** (90-day lifecycle): ~$0.01/month (negligible)
- **Local CAR files** (3-day retention): $0 (uses instance storage)
- **Hourly snapshots**: $0 (stored in IPFS)

**Backup subtotal**: ~$0.54/month

### Total Monthly Cost: ~$17.50-18.50/month

**Cost breakdown:**
- Core infrastructure: 97%
- Backup infrastructure: 3%

**Additional notes:**
- EBS snapshot cost is incremental (only changed blocks), so actual cost may be lower
- S3 cost assumes ~20MB CAR files daily
- Data transfer costs only apply when downloading backups (disaster recovery)

## Teardown

To completely remove all AWS resources:

### 1. Terminate EC2 Instance

```bash
# Get instance ID from deployment-info.txt or:
INSTANCE_ID=$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=arke-ipfs-server" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text)

# Terminate instance (also deletes EBS volume due to DeleteOnTermination=true)
aws ec2 terminate-instances --instance-ids $INSTANCE_ID

# Wait for termination
aws ec2 wait instance-terminated --instance-ids $INSTANCE_ID
```

### 2. Delete Security Group

```bash
# Delete security group (must wait for instance termination first)
aws ec2 delete-security-group --group-name arke-ipfs-sg
```

### 3. Delete SSH Key Pair

```bash
# Delete key pair from AWS
aws ec2 delete-key-pair --key-name arke-ipfs-key

# Remove local key file
rm ~/.ssh/arke-ipfs-key.pem
```

### 4. Delete S3 Backup Bucket

```bash
# Get account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET_NAME="arke-ipfs-backups-${ACCOUNT_ID}"

# Delete all objects in bucket (required before bucket deletion)
aws s3 rm s3://$BUCKET_NAME --recursive

# Delete all object versions (if versioning enabled)
aws s3api delete-objects \
    --bucket $BUCKET_NAME \
    --delete "$(aws s3api list-object-versions \
        --bucket $BUCKET_NAME \
        --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' \
        --output json)"

# Delete bucket
aws s3api delete-bucket --bucket $BUCKET_NAME --region us-east-1
```

### 5. Delete IAM Role and Policy

```bash
# Detach policy from role
aws iam detach-role-policy \
    --role-name arke-ipfs-ec2-backup-role \
    --policy-arn $(aws iam list-attached-role-policies \
        --role-name arke-ipfs-ec2-backup-role \
        --query 'AttachedPolicies[0].PolicyArn' \
        --output text)

# Delete custom policy
POLICY_ARN=$(aws iam list-policies \
    --scope Local \
    --query 'Policies[?PolicyName==`arke-ipfs-s3-backup-policy`].Arn' \
    --output text)
aws iam delete-policy --policy-arn $POLICY_ARN

# Remove instance profile
aws iam remove-role-from-instance-profile \
    --instance-profile-name arke-ipfs-ec2-backup-role \
    --role-name arke-ipfs-ec2-backup-role
aws iam delete-instance-profile \
    --instance-profile-name arke-ipfs-ec2-backup-role

# Delete role
aws iam delete-role --role-name arke-ipfs-ec2-backup-role
```

### 6. Delete EBS Snapshots

```bash
# List all DLM-created snapshots
aws ec2 describe-snapshots \
    --owner-ids self \
    --filters "Name=tag:SnapshotType,Values=DLM-Automated" \
    --query 'Snapshots[].SnapshotId' \
    --output text

# Delete each snapshot
for SNAPSHOT_ID in $(aws ec2 describe-snapshots \
    --owner-ids self \
    --filters "Name=tag:SnapshotType,Values=DLM-Automated" \
    --query 'Snapshots[].SnapshotId' \
    --output text); do
    aws ec2 delete-snapshot --snapshot-id $SNAPSHOT_ID
done
```

### 7. Delete DLM Lifecycle Policy

```bash
# Get policy ID
POLICY_ID=$(aws dlm get-lifecycle-policies \
    --region us-east-1 \
    --query "Policies[?Description=='Daily EBS snapshots for Arke IPFS server'].PolicyId" \
    --output text)

# Delete policy
aws dlm delete-lifecycle-policy \
    --policy-id $POLICY_ID \
    --region us-east-1
```

### 8. Clean Up Local Files

```bash
# Remove deployment info
rm deployment-info.txt

# Optional: Clean up local backups
rm -rf backups/*.car
rm -rf snapshots/*.json
```

### Complete Teardown Script

For convenience, use the automated teardown script:

```bash
./scripts/teardown-aws.sh
```

This script performs all cleanup steps above in the correct order.

**Warning**: This is destructive and irreversible. All data and backups will be permanently deleted.

## Support

For documentation:
- `README.md` - Basic operations
- `API_WALKTHROUGH.md` - API integration guide
- `DISASTER_RECOVERY.md` - DR procedures
- `CLAUDE.md` - Project architecture

For issues, consult AWS or Cloudflare documentation.
