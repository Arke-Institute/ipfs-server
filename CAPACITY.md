# Capacity Planning and Network Configuration

## Private Network Mode

This IPFS node operates as a **private storage backend** with public network participation disabled. This dramatically reduces resource consumption.

### Configuration (Automated via Init Script)

The private mode configuration is automatically applied on every container startup via `ipfs-init.d/001-private-mode.sh`. This ensures settings persist across container restarts and volume recreations.

| Setting | Value | Purpose |
|---------|-------|---------|
| `Routing.Type` | `none` | Disables DHT participation |
| `Bootstrap` | `[]` (empty) | No public peer discovery |
| `Addresses.Announce` | `[]` | No address announcements |
| `Addresses.NoAnnounce` | `[all]` | Block all address announcements |
| `Swarm.ConnMgr.LowWater` | `0` | Zero minimum connections |
| `Swarm.ConnMgr.HighWater` | `10` | Very low max connections |
| `Swarm.RelayClient.Enabled` | `false` | No relay client |
| `Swarm.RelayService.Enabled` | `false` | No relay service |
| `Swarm.EnableHolePunching` | `false` | No hole punching |
| `AutoNAT.ServiceMode` | `disabled` | No NAT detection |
| `Swarm.ResourceMgr.Enabled` | `false` | Disabled resource manager |
| Port 4001 | `127.0.0.1:4001` | Swarm bound to localhost only |

### Impact

| Metric | Public Mode | Private Mode | Reduction |
|--------|-------------|--------------|-----------|
| Peer connections | ~1,000 | 0 | 100% |
| Memory (IPFS) | ~950 MB | ~50-170 MB | 80-95% |
| CPU baseline | 40-50% | <10% | 80%+ |
| Bandwidth | 500+ KB/s | ~0 | 99%+ |

### Why This Matters

With public DHT enabled, the node was routing queries for the entire IPFS network (2.7M+ messages) while storing/retrieving zero blocks for others. This consumed resources without benefit for a private storage backend.

---

## Server Specifications

**Instance**: t3.small (AWS EC2)
- vCPU: 2
- RAM: 2 GB
- Disk: 30 GB EBS (gp3)

---

## Capacity Limits

### Storage

| Threshold | Objects | Data Size | Status |
|-----------|---------|-----------|--------|
| Current | ~470K | ~4 GB | OK |
| Comfortable | ~2M | ~20 GB | Safe |
| Maximum | ~2.5M | ~25 GB | Near limit |
| Disk full | - | 30 GB | Critical |

**Scaling**: Increase EBS volume size via AWS console.

### Request Throughput

| Load | Requests/sec | CPU | Status |
|------|--------------|-----|--------|
| Current | ~17 | ~6% | Normal |
| 10x | ~170 | ~60% | OK |
| Max sustained | ~300-500 | 80%+ | Limit |

### Operation Latency

| Operation | Latency | Throughput |
|-----------|---------|------------|
| Tip file read | ~4 ms | ~250/sec |
| Manifest DAG get | ~11 ms | ~90/sec |
| Manifest DAG put | ~11 ms | ~90/sec |
| Small file add (<100 KB) | ~20-50 ms | ~50/sec |
| Large file add (1 MB) | ~230 ms | ~4/sec |
| Large file add (10 MB) | ~2 sec | ~0.5/sec |

### Hourly Capacity (Conservative)

| Operation | Current/hr | Safe Max/hr |
|-----------|-----------|-------------|
| Tip reads | 19K | 500K |
| Tip writes | 4K | 200K |
| Manifest creates | 4K | 200K |
| File adds (small) | 3K | 100K |
| File adds (1 MB) | - | 10K |

---

## Scaling Recommendations

### 10x Growth (Current → 30K PIs)
No changes needed. Current instance handles this.

### 100x Growth (Current → 300K PIs)
- Upgrade to **t3.medium** (4 GB RAM) - ~$30/mo additional
- Increase EBS to **100 GB** - ~$7/mo additional

### 1000x Growth (Current → 3M PIs)
- Upgrade to **t3.large** or **t3.xlarge**
- Consider EBS io1/io2 for IOPS
- May need read replicas or load balancing

---

## Diagnostic Commands

### Check current state
```bash
# Peer count (should be 0-5)
curl -s -X POST http://localhost:5001/api/v0/swarm/peers | jq '.Peers | length'

# Resource usage
docker stats --no-stream ipfs-node-prod

# Repo size
curl -s -X POST http://localhost:5001/api/v0/repo/stat | jq '{RepoSize, NumObjects}'
```

### Check configuration
```bash
curl -s -X POST http://localhost:5001/api/v0/config/show | jq '{
  Routing: .Routing.Type,
  Bootstrap: (.Bootstrap | length),
  ConnMgr: .Swarm.ConnMgr
}'
```

### Monitor request rate
```bash
# Requests in last hour
docker logs ipfs-nginx --since 1h 2>&1 | grep -c "POST /api/v0"
```

---

## Troubleshooting High Resource Usage

> **Note**: With the automated init script (`ipfs-init.d/001-private-mode.sh`), private mode is configured on every startup. These steps should rarely be needed.

If CPU/memory spikes unexpectedly:

1. **Check peer count** - Should be 0
   ```bash
   curl -s -X POST http://localhost:5001/api/v0/swarm/peers | jq '.Peers | length'
   ```

2. **If any peers connected**, check if init script ran:
   ```bash
   # Check container logs for init script output
   docker logs ipfs-node-prod 2>&1 | grep "Private mode"

   # Verify routing is disabled
   curl -s -X POST http://localhost:5001/api/v0/config/show | jq '.Routing.Type'
   # Should return "none"
   ```

3. **If init script didn't run**, verify mount:
   ```bash
   docker exec ipfs-node-prod ls -la /container-init.d/
   # Should show 001-private-mode.sh
   ```

4. **Disconnect stray peers** (if any):
   ```bash
   peers=$(curl -s -X POST http://localhost:5001/api/v0/swarm/peers | jq -r '.Peers[].Peer')
   for p in $peers; do
     curl -s -X POST "http://localhost:5001/api/v0/swarm/disconnect?arg=/p2p/$p" > /dev/null
   done
   ```

5. **Force restart** to re-run init script:
   ```bash
   docker compose -f docker-compose.nginx.yml restart ipfs
   ```

---

## See Also

- [MONITORING.md](MONITORING.md) - Automated maintenance and alerting
- [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) - Backup and restore procedures
- [DEPLOYMENT.md](DEPLOYMENT.md) - Initial server setup
