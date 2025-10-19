# Test Suite

Automated tests for verifying Arke IPFS deployment.

## Test Script

**`test-deployment.sh`** - Comprehensive end-to-end deployment test

### What It Tests

**API Service (port 3000):**
- Health check endpoint
- Index pointer retrieval
- Events append
- Events query

**IPFS RPC API (port 5001):**
- Version check
- File upload (`/api/v0/add`)
- DAG put/get (manifest storage/retrieval)
- MFS operations (mkdir, write, read)
- Pin management (pin update)
- Repo stats

**Gateway (port 8080):**
- Content retrieval by CID

**Complete Workflow:**
- Upload file → Get CID
- Create manifest → Store as DAG
- Write .tip file to MFS
- Append event to chain
- Retrieve content via gateway

## Usage

### Test Production Deployment

```bash
# Run tests only (leaves test data)
./tests/test-deployment.sh

# Run tests and clean up test data
./tests/test-deployment.sh --cleanup
```

Tests against:
- API: `https://ipfs-api.arke.institute`
- Gateway: `https://ipfs.arke.institute`

### Test Local Development

```bash
API_ENDPOINT=http://localhost:5001 \
GATEWAY_ENDPOINT=http://localhost:8080 \
./tests/test-deployment.sh --cleanup
```

### Test Custom Endpoints

```bash
API_ENDPOINT=https://custom-api.example.com \
GATEWAY_ENDPOINT=https://custom-gateway.example.com \
./tests/test-deployment.sh --cleanup
```

### Cleanup Flag

The `--cleanup` flag performs safe cleanup after tests complete successfully:

**What gets cleaned up:**
- ✅ Removes `.tip` file from MFS index (entity no longer discoverable)
- ✅ Unpins test manifests (allows IPFS garbage collection)
- ✅ Test files auto-GC (uploaded with `pin=false`)

**What remains:**
- ❌ Test events stay in chain (immutable, ~200 bytes each, harmless)

**Why cleanup is safe:**
- Doesn't break event chain integrity
- Test entity removed from index (won't show in queries)
- Unpinned content eventually garbage collected by IPFS
- Minimal impact: events are tiny and don't reference non-existent data

## Output

The script provides colored output:
- ✅ **Green**: Test passed
- ❌ **Red**: Test failed
- **Blue**: Section headers
- **Yellow**: Important values

Example output:
```
========================================
  Arke IPFS Deployment Test Suite
========================================

API Endpoint:     https://ipfs-api.arke.institute
Gateway Endpoint: https://ipfs.arke.institute

>>> Test 1: API Service Health Check
✓ Health endpoint returns 200 and status=healthy

>>> Test 2: IPFS RPC - Version Check
✓ IPFS version: 0.38.1

>>> Test 3: IPFS RPC - File Upload
✓ File uploaded: bafkreif...

...

========================================
  Test Summary
========================================

Tests Passed: 14
Tests Failed: 0

✓ All tests passed!

Test Entity Created:
  PI:           01TEST1729351234000000000
  Manifest CID: bafyreiabc...
  Event CID:    bafyreidef...
```

## Exit Codes

- `0` - All tests passed
- `1` - One or more tests failed

## Requirements

- `curl` - HTTP requests
- `jq` - JSON parsing
- `mktemp` - Temporary file creation

Install dependencies:
```bash
# macOS
brew install jq

# Ubuntu/Debian
sudo apt-get install jq
```

## CI/CD Integration

Use in continuous integration:

```yaml
# GitHub Actions example
- name: Test IPFS Deployment
  run: ./tests/test-deployment.sh
```

```bash
# GitLab CI example
test:
  script:
    - ./tests/test-deployment.sh
```

## Troubleshooting

### All tests fail

Check if endpoints are accessible:
```bash
curl https://ipfs-api.arke.institute/health
curl https://ipfs.arke.institute/ipfs/bafybeiczsscdsbs7ffqz55asqdf3smv6klcw3gofszvwlyarci47bgf354
```

### Health check fails

Check if API service is running:
```bash
ssh -i ~/.ssh/arke-ipfs-key.pem ubuntu@<ip> 'docker ps'
```

### IPFS RPC tests fail

Check if IPFS is accessible via nginx:
```bash
curl -X POST https://ipfs-api.arke.institute/api/v0/version
```

### Gateway tests fail

Check if gateway is accessible:
```bash
curl https://ipfs.arke.institute/ipfs/QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG
```

## Test Data Cleanup

The test creates temporary data:
- Random PI starting with `01TEST`
- Test manifests (pinned)
- Test .tip files in MFS
- Test events in event chain

**Recommended:** Use `--cleanup` flag to automatically clean up after tests:
```bash
./tests/test-deployment.sh --cleanup
```

This removes the test entity from the index and unpins manifests, allowing IPFS to garbage collect them. Test events (~200 bytes) remain in the immutable event chain but don't affect system operation.
