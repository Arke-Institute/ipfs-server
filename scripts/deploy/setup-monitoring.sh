#!/bin/bash
# Setup Monitoring and Automated Maintenance for Arke IPFS Server
#
# This script configures:
# 1. CloudWatch alarms for automatic instance recovery/reboot
# 2. Weekly scheduled reboots via cron
# 3. Daily CAR backup exports via cron
#
# USAGE:
#   ./scripts/deploy/setup-monitoring.sh <instance-id>
#
# EXAMPLE:
#   ./scripts/deploy/setup-monitoring.sh i-0443444abcd3ed689
#
# REQUIREMENTS:
# - AWS CLI configured with proper credentials
# - SSH access to the EC2 instance (for cron setup)
# - EC2 instance must be running
#
# WHAT IT DOES:
# - Creates CloudWatch alarm for auto-reboot on instance check failure
# - Creates CloudWatch alarm for auto-recovery on system check failure
# - Adds cron job for weekly maintenance reboot (Sundays 4 AM UTC)
# - Adds cron job for daily CAR backup export (2 AM UTC)
#

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <instance-id>"
    echo "Example: $0 i-0443444abcd3ed689"
    exit 1
fi

INSTANCE_ID="$1"
REGION="${AWS_REGION:-us-east-1}"
KEY_FILE="$HOME/.ssh/arke-ipfs-key.pem"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Arke IPFS - Monitoring & Maintenance Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Verify instance exists
log "Verifying instance $INSTANCE_ID..."
if ! aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" > /dev/null 2>&1; then
    echo "Error: Instance $INSTANCE_ID not found in region $REGION"
    exit 1
fi
success "Instance verified"
echo ""

# Get instance IP for SSH
PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

if [ -z "$PUBLIC_IP" ] || [ "$PUBLIC_IP" = "None" ]; then
    echo "Error: Could not get public IP for instance $INSTANCE_ID"
    exit 1
fi

log "Instance IP: $PUBLIC_IP"
echo ""

#
# STEP 1: CloudWatch Alarms
#
log "Setting up CloudWatch alarms..."

# Alarm 1: Auto-reboot on instance check failure
log "  Creating auto-reboot alarm for instance status checks..."
aws cloudwatch put-metric-alarm \
    --alarm-name "arke-ipfs-auto-reboot" \
    --alarm-description "Auto-reboot IPFS instance on instance status check failure" \
    --namespace AWS/EC2 \
    --metric-name StatusCheckFailed_Instance \
    --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
    --statistic Maximum \
    --period 60 \
    --evaluation-periods 2 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --alarm-actions "arn:aws:automate:${REGION}:ec2:reboot" \
    --region "$REGION" > /dev/null

success "  ✓ Auto-reboot alarm created"

# Alarm 2: Auto-recover on system check failure
log "  Creating auto-recovery alarm for system status checks..."
aws cloudwatch put-metric-alarm \
    --alarm-name "arke-ipfs-system-recover" \
    --alarm-description "Auto-recover IPFS instance on system status check failure" \
    --namespace AWS/EC2 \
    --metric-name StatusCheckFailed_System \
    --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
    --statistic Maximum \
    --period 60 \
    --evaluation-periods 2 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --alarm-actions "arn:aws:automate:${REGION}:ec2:recover" \
    --region "$REGION" > /dev/null

success "  ✓ Auto-recovery alarm created"
echo ""

#
# STEP 2: Cron Jobs
#
log "Setting up cron jobs on EC2 instance..."

# Check SSH access
if [ ! -f "$KEY_FILE" ]; then
    warn "SSH key not found at $KEY_FILE"
    warn "Skipping cron setup - please configure manually"
else
    log "  Testing SSH connection..."
    if ! ssh -i "$KEY_FILE" -o ConnectTimeout=5 -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" "echo 'Connected'" > /dev/null 2>&1; then
        warn "Could not connect via SSH to $PUBLIC_IP"
        warn "Skipping cron setup - please configure manually"
    else
        success "  ✓ SSH connection established"

        log "  Adding weekly reboot cron job (Sundays 4 AM UTC)..."
        ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" << 'EOF'
# Add weekly reboot (remove old entry if exists)
(crontab -l 2>/dev/null | grep -v "weekly-reboot" ; echo "0 4 * * 0 /usr/sbin/shutdown -r +1 'Weekly maintenance reboot' >> /var/log/arke-weekly-reboot.log 2>&1 # weekly-reboot") | crontab -
EOF
        success "  ✓ Weekly reboot scheduled"

        log "  Adding daily CAR backup cron job (2 AM UTC)..."
        ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" << 'EOF'
# Add daily CAR export (remove old entry if exists)
(crontab -l 2>/dev/null | grep -v "daily-car-export" ; echo "0 2 * * * /home/ubuntu/ipfs-server/scripts/daily-car-export.sh >> /var/log/arke-backup.log 2>&1 # daily-car-export") | crontab -
EOF
        success "  ✓ Daily CAR backup scheduled"

        log "  Verifying cron configuration..."
        ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" "crontab -l"
        echo ""
    fi
fi

#
# SUMMARY
#
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "CloudWatch Alarms:"
echo "  ✓ arke-ipfs-auto-reboot - Auto-reboot on instance check failure"
echo "  ✓ arke-ipfs-system-recover - Auto-recover on system check failure"
echo ""
echo "Scheduled Maintenance:"
echo "  ✓ Weekly reboot - Sundays at 4:00 AM UTC"
echo "  ✓ Daily CAR backup - Every day at 2:00 AM UTC"
echo ""
echo "View alarms in AWS Console:"
echo "  https://console.aws.amazon.com/cloudwatch/home?region=${REGION}#alarmsV2:"
echo ""
echo "Monitor logs on instance:"
echo "  ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
echo "  tail -f /var/log/arke-backup.log          # CAR backup logs"
echo "  tail -f /var/log/arke-weekly-reboot.log   # Reboot logs"
echo ""
