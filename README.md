# IPFS/Kubo Node

A containerized IPFS (Kubo) node with local datastore for EC2 deployment. This provides the foundational IPFS layer for the Arke API Service to communicate with directly via Kubo's HTTP RPC API.

## Architecture

```
[ API Service (port 4000) ]
         ↓
    HTTP RPC calls
         ↓
[ Kubo Node (port 5001) ]
         ↓
   [ Local Datastore ]
```

The API Service will make direct calls to Kubo's HTTP RPC API endpoints such as:
- `/api/v0/add` - Add content
- `/api/v0/dag/put` - Store DAG nodes
- `/api/v0/dag/get` - Retrieve DAG nodes
- `/api/v0/files/*` - MFS operations (for .tip files)
- `/api/v0/pin/*` - Pin management
- `/api/v0/cat` - Retrieve content

## Quick Start

### Local Development

1. **Start Kubo node**:
```bash
docker compose up -d
```

2. **Verify it's running**:
```bash
# Check container status
docker compose ps

# Check Kubo version
curl -X POST http://localhost:5001/api/v0/version

# Test adding content
echo "Hello IPFS" | curl -X POST -F "file=@-" http://localhost:5001/api/v0/add
```

3. **Stop the node**:
```bash
docker compose down
```

### Ports

- **4001**: P2P (Swarm) - for connecting to IPFS network
- **5001**: HTTP RPC API - for your API Service to connect to
- **8080**: HTTP Gateway - for browsing IPFS content via web browser

## EC2 Deployment

### Prerequisites

- AWS EC2 instance (Ubuntu 22.04 LTS recommended)
- Docker and Docker Compose installed
- Security group allowing:
  - Port 22 (SSH)
  - Port 4001 (IPFS P2P)
  - Port 5001 (localhost only - for API Service)

### Deployment Steps

1. **SSH to EC2**:
```bash
ssh -i your-key.pem ubuntu@your-ec2-ip
```

2. **Install Docker**:
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Log out and back in
exit
```

3. **Clone repository**:
```bash
git clone <repository-url> ipfs-node
cd ipfs-node
```

4. **Start production node**:
```bash
docker compose -f docker-compose.prod.yml up -d
```

5. **Verify**:
```bash
docker compose -f docker-compose.prod.yml ps
curl -X POST http://localhost:5001/api/v0/version
```

### Security Notes

In `docker-compose.prod.yml`, the RPC API (port 5001) and Gateway (port 8080) are bound to `127.0.0.1` only, meaning they're only accessible from localhost. This ensures only your API Service running on the same EC2 instance can communicate with Kubo.

Only port 4001 (P2P) is publicly accessible for IPFS network connectivity.

## Data Persistence

IPFS data is stored in Docker volumes:
- `ipfs_data` - Main IPFS repository (blocks, config, keys)
- `ipfs_staging` - Temporary staging area

### Backup

```bash
# Stop node
docker compose -f docker-compose.prod.yml stop

# Backup volume
docker run --rm \
  -v ipfs-server_ipfs_data:/data:ro \
  -v $(pwd):/backup \
  alpine \
  tar czf /backup/ipfs-backup-$(date +%Y%m%d).tar.gz -C /data .

# Restart node
docker compose -f docker-compose.prod.yml start
```

### Restore

```bash
# Stop node
docker compose -f docker-compose.prod.yml down

# Remove old volume
docker volume rm ipfs-server_ipfs_data

# Create new volume
docker volume create ipfs-server_ipfs_data

# Restore backup
docker run --rm \
  -v ipfs-server_ipfs_data:/data \
  -v $(pwd):/backup:ro \
  alpine \
  tar xzf /backup/ipfs-backup-20240101.tar.gz -C /data

# Start node
docker compose -f docker-compose.prod.yml up -d
```

## Configuration

### IPFS Profiles

The node runs with `IPFS_PROFILE=server` which is optimized for server deployments. Other profiles:
- `server` - Recommended for production (reduced resource usage)
- `lowpower` - Minimal resource usage
- `randomports` - Use random ports (for testing)

To change profile, edit `docker-compose.yml`:
```yaml
environment:
  - IPFS_PROFILE=lowpower
```

### Resource Limits

Production deployment (`docker-compose.prod.yml`) includes resource limits:
- CPU: 1-2 cores
- Memory: 1-2 GB

Adjust based on your workload in the `deploy.resources` section.

## Monitoring

### View Logs

```bash
# Follow logs
docker compose logs -f

# Last 100 lines
docker compose logs --tail=100

# Production
docker compose -f docker-compose.prod.yml logs -f
```

### Check Status

```bash
# Container status
docker compose ps

# IPFS peer ID
curl -X POST http://localhost:5001/api/v0/id | jq .

# Repository stats
curl -X POST http://localhost:5001/api/v0/repo/stat | jq .

# Connected peers
curl -X POST http://localhost:5001/api/v0/swarm/peers | jq .
```

### Health Check

```bash
# Simple health check
docker exec ipfs-node ipfs id

# Or via API
curl -X POST http://localhost:5001/api/v0/version
```

## Common Operations

### Add Content

```bash
# Add file
curl -X POST -F "file=@myfile.txt" http://localhost:5001/api/v0/add

# Add with CIDv1
curl -X POST -F "file=@myfile.txt" \
  "http://localhost:5001/api/v0/add?cid-version=1"
```

### Retrieve Content

```bash
# Get content
curl -X POST "http://localhost:5001/api/v0/cat?arg=QmYourCID"

# Via gateway
curl "http://localhost:8080/ipfs/QmYourCID"
```

### Pin Management

```bash
# Pin content
curl -X POST "http://localhost:5001/api/v0/pin/add?arg=QmYourCID"

# List pins
curl -X POST "http://localhost:5001/api/v0/pin/ls"

# Unpin content
curl -X POST "http://localhost:5001/api/v0/pin/rm?arg=QmYourCID"
```

### MFS Operations

```bash
# Create directory
curl -X POST "http://localhost:5001/api/v0/files/mkdir?arg=/mydir&parents=true"

# Write file
echo "content" | curl -X POST \
  -F "file=@-" \
  "http://localhost:5001/api/v0/files/write?arg=/mydir/file.txt&create=true"

# Read file
curl -X POST "http://localhost:5001/api/v0/files/read?arg=/mydir/file.txt"

# List directory
curl -X POST "http://localhost:5001/api/v0/files/ls?arg=/mydir"
```

## Troubleshooting

### Node Won't Start

```bash
# Check logs
docker compose logs

# Check if ports are in use
lsof -i :4001
lsof -i :5001
lsof -i :8080

# Remove and recreate
docker compose down -v
docker compose up -d
```

### Out of Disk Space

```bash
# Check disk usage
df -h

# Run garbage collection
curl -X POST http://localhost:5001/api/v0/repo/gc

# Check repo size
curl -X POST http://localhost:5001/api/v0/repo/stat | jq .RepoSize
```

### Performance Issues

1. **Increase resources** in docker-compose file
2. **Reduce connections**:
```bash
ipfs config --json Swarm.ConnMgr.HighWater 500
ipfs config --json Swarm.ConnMgr.LowWater 200
```
3. **Disable bandwidth logging**:
```bash
ipfs config --json Swarm.DisableBandwidthMetrics true
```

## API Reference

Full Kubo HTTP RPC API documentation:
- https://docs.ipfs.tech/reference/kubo/rpc/

Common endpoints for your API Service:
- `/api/v0/add` - Add files
- `/api/v0/cat` - Retrieve files
- `/api/v0/dag/put` - Store DAG nodes (for manifests)
- `/api/v0/dag/get` - Retrieve DAG nodes
- `/api/v0/files/*` - MFS operations (for .tip files)
- `/api/v0/pin/add` - Pin content
- `/api/v0/pin/rm` - Unpin content
- `/api/v0/pin/update` - Swap pins efficiently

## Integration with API Service

Your API Service should connect to Kubo at:
- **Local development**: `http://localhost:5001`
- **Docker Compose**: `http://ipfs:5001` (if API Service is also containerized)
- **EC2 production**: `http://localhost:5001`

Example API Service configuration:
```javascript
const IPFS_API_URL = process.env.IPFS_API_URL || 'http://localhost:5001';

// Add content
const response = await fetch(`${IPFS_API_URL}/api/v0/add`, {
  method: 'POST',
  body: formData
});
```

## Documentation

Additional documentation in this repository:
- `IPFS_Complete_Guide.md` - Comprehensive IPFS operations guide
- `IPFS_API_Complete_Guide.md` - Complete HTTP API reference
- `IPFS_S3_Datastore_Guide.md` - S3 datastore setup (if needed)

Official resources:
- Kubo Documentation: https://docs.ipfs.tech/
- Kubo GitHub: https://github.com/ipfs/kubo
- IPFS Forums: https://discuss.ipfs.tech/

## License

MIT
