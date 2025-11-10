# Monitoring and Automated Maintenance

This document describes the monitoring, alerting, and automated maintenance systems configured for the Arke IPFS Server.

## Overview

The server is protected by multiple layers of automation to ensure high availability and data safety:

1. **CloudWatch Alarms** - Automatic recovery from failures
2. **Scheduled Reboots** - Weekly maintenance to prevent resource buildup
3. **Automated Backups** - Daily CAR exports to S3

## CloudWatch Alarms

### Auto-Reboot on Instance Failure

**Alarm Name**: `arke-ipfs-auto-reboot`

- **Monitors**: EC2 Instance Status Checks
- **Trigger**: 2 consecutive failed checks (2 minutes)
- **Action**: Automatic instance reboot via AWS
- **Purpose**: Recovers from software hangs, memory issues, or OS problems

**When it triggers**:
- Out of memory (OOM) situations
- Kernel panics or system hangs
- Network stack failures
- Any condition that makes the instance unresponsive

**Recovery time**: Typically 2-4 minutes from failure detection to full service restoration

### Auto-Recover on System Failure

**Alarm Name**: `arke-ipfs-system-recover`

- **Monitors**: EC2 System Status Checks
- **Trigger**: 2 consecutive failed checks (2 minutes)
- **Action**: Automatic instance recovery (hardware-level)
- **Purpose**: Recovers from underlying hardware failures

**When it triggers**:
- Physical hardware failures
- Network connectivity issues at AWS infrastructure level
- Power delivery problems

**Recovery time**: Typically 5-10 minutes (involves instance migration to new hardware)

### Setup

Alarms are configured using the monitoring setup script:

```bash
./scripts/deploy/setup-monitoring.sh <instance-id>
```

Or manually via AWS CLI:

```bash
# Auto-reboot alarm
aws cloudwatch put-metric-alarm \
  --alarm-name arke-ipfs-auto-reboot \
  --alarm-description "Auto-reboot IPFS instance on instance status check failure" \
  --namespace AWS/EC2 \
  --metric-name StatusCheckFailed_Instance \
  --dimensions Name=InstanceId,Value=i-XXXXXXXXX \
  --statistic Maximum \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions arn:aws:automate:us-east-1:ec2:reboot \
  --region us-east-1

# Auto-recover alarm
aws cloudwatch put-metric-alarm \
  --alarm-name arke-ipfs-system-recover \
  --alarm-description "Auto-recover IPFS instance on system status check failure" \
  --namespace AWS/EC2 \
  --metric-name StatusCheckFailed_System \
  --dimensions Name=InstanceId,Value=i-XXXXXXXXX \
  --statistic Maximum \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions arn:aws:automate:us-east-1:ec2:recover \
  --region us-east-1
```

### Viewing Alarm Status

**AWS Console**:
```
https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#alarmsV2:
```

**AWS CLI**:
```bash
aws cloudwatch describe-alarms \
  --alarm-names arke-ipfs-auto-reboot arke-ipfs-system-recover \
  --region us-east-1
```

## Scheduled Maintenance

### Weekly Reboot

**Schedule**: Every Sunday at 4:00 AM UTC (11:00 PM EST Saturday)

**Cron entry**:
```cron
0 4 * * 0 /usr/sbin/shutdown -r +1 'Weekly maintenance reboot' >> /var/log/arke-weekly-reboot.log 2>&1
```

**Purpose**:
- Prevents resource leaks from long-running processes
- Clears accumulated memory pressure
- Ensures clean state recovery
- Applies kernel updates that require reboot

**Duration**: ~2 minutes downtime

**Logs**: `/var/log/arke-weekly-reboot.log`

### Daily CAR Backup

**Schedule**: Every day at 2:00 AM UTC

**Cron entry**:
```cron
0 2 * * * /home/ubuntu/ipfs-server/scripts/daily-car-export.sh >> /var/log/arke-backup.log 2>&1
```

**What it does**:
1. Reads latest snapshot CID from `/home/ubuntu/ipfs-server/snapshots/latest.json`
2. Calls Python DR module: `docker exec ipfs-api python3 -m dr.export_car <snapshot-cid>`
3. Exports complete snapshot to CAR file (includes all manifests, components, and events)
4. Uploads CAR file + metadata JSON to S3

**S3 Location**:
```
s3://arke-ipfs-backups-{account-id}/backups/{instance-id}/
├── arke-{seq}-{timestamp}.car
└── arke-{seq}-{timestamp}.json
```

**Logs**: `/var/log/arke-backup.log`

**Script**: `scripts/daily-car-export.sh`

## Snapshot Build Automation

Snapshots are built automatically every hour by the API service (independent of the daily CAR export).

**Scheduler**: Python AsyncIO background task in API service
**Frequency**: Every 60 minutes
**Location**: `api/main.py` (snapshot scheduler)
**Output**: `/home/ubuntu/ipfs-server/snapshots/snapshot-{seq}.json`

**How it works**:
1. Reads index pointer from IPFS MFS to get current event chain head
2. If new events exist since last snapshot, builds incremental snapshot
3. Stores snapshot as `dag-json` in IPFS
4. Saves metadata to `snapshots/snapshot-{seq}.json`
5. Updates `snapshots/latest.json` symlink

**View logs**:
```bash
docker exec ipfs-api cat /app/logs/snapshot-build.log
```

## Log Files

| Log File | Purpose | Location |
|----------|---------|----------|
| `/var/log/arke-backup.log` | Daily CAR backup operations | EC2 instance |
| `/var/log/arke-weekly-reboot.log` | Weekly reboot history | EC2 instance |
| `/app/logs/snapshot-build.log` | Snapshot build operations | Inside ipfs-api container |
| Docker logs | Container runtime logs | `docker logs <container>` |

## Monitoring Commands

### Check cron jobs
```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<instance-ip>
crontab -l
```

### View backup logs
```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<instance-ip>
tail -f /var/log/arke-backup.log
```

### Check S3 backups
```bash
aws s3 ls s3://arke-ipfs-backups-{account-id}/backups/{instance-id}/ --region us-east-1
```

### View CloudWatch alarm history
```bash
aws cloudwatch describe-alarm-history \
  --alarm-name arke-ipfs-auto-reboot \
  --max-records 10 \
  --region us-east-1
```

### Check instance status
```bash
aws ec2 describe-instance-status \
  --instance-ids i-XXXXXXXXX \
  --region us-east-1
```

## Troubleshooting

### Alarm not triggering

1. Verify alarm exists and is enabled:
   ```bash
   aws cloudwatch describe-alarms --alarm-names arke-ipfs-auto-reboot --region us-east-1
   ```

2. Check alarm state (should be "OK" when healthy, "ALARM" when triggered):
   ```bash
   aws cloudwatch describe-alarms \
     --alarm-names arke-ipfs-auto-reboot \
     --query 'MetricAlarms[0].StateValue' \
     --output text
   ```

### Backup not running

1. Check cron is configured:
   ```bash
   crontab -l | grep daily-car-export
   ```

2. Check script exists and is executable:
   ```bash
   ls -la /home/ubuntu/ipfs-server/scripts/daily-car-export.sh
   ```

3. Check logs for errors:
   ```bash
   tail -50 /var/log/arke-backup.log
   ```

4. Run manually to test:
   ```bash
   /home/ubuntu/ipfs-server/scripts/daily-car-export.sh
   ```

### Weekly reboot not happening

1. Check cron entry:
   ```bash
   crontab -l | grep weekly-reboot
   ```

2. Check reboot logs:
   ```bash
   tail /var/log/arke-weekly-reboot.log
   ```

3. Verify system time is correct (cron uses system time):
   ```bash
   date -u  # Should show UTC time
   ```

## Best Practices

1. **Monitor alarm state regularly** - Ensure alarms remain in "OK" state
2. **Review backup logs weekly** - Verify CAR exports are succeeding
3. **Test recovery procedures** - Periodically test restore from CAR backup
4. **Keep documentation updated** - Document any changes to monitoring configuration
5. **Set up SNS notifications** (optional) - Get email alerts when alarms trigger

## SNS Email Notifications (Optional)

To receive email notifications when alarms trigger:

1. Create SNS topic:
   ```bash
   aws sns create-topic --name arke-ipfs-alerts --region us-east-1
   ```

2. Subscribe your email:
   ```bash
   aws sns subscribe \
     --topic-arn arn:aws:sns:us-east-1:{account-id}:arke-ipfs-alerts \
     --protocol email \
     --notification-endpoint your-email@example.com \
     --region us-east-1
   ```

3. Update alarms to include SNS topic:
   ```bash
   aws cloudwatch put-metric-alarm \
     --alarm-name arke-ipfs-auto-reboot \
     --alarm-actions \
       arn:aws:automate:us-east-1:ec2:reboot \
       arn:aws:sns:us-east-1:{account-id}:arke-ipfs-alerts \
     [... other parameters ...]
   ```

## See Also

- [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) - Full DR strategy and restore procedures
- [DEPLOYMENT.md](DEPLOYMENT.md) - Deployment and infrastructure setup
- [README.md](README.md) - General project overview and operations
