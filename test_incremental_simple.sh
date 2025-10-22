#!/bin/bash

# Simple incremental snapshot test
# Creates progressively more entities and tests timing

set -e

echo "======================================================================"
echo "INCREMENTAL SNAPSHOT TEST"
echo "======================================================================"

# Test 1: Build initial snapshot from existing test data (3 entities, 6 events)
echo ""
echo "TEST 1: Initial snapshot (full traversal)"
echo "----------------------------------------------------------------------"
time docker exec ipfs-api python3 -m dr.build_snapshot

# Test 2: Immediately rebuild (should skip - no new events)
echo ""
echo "TEST 2: Immediate rebuild (should skip - no new events)"
echo "----------------------------------------------------------------------"
time docker exec ipfs-api python3 -m dr.build_snapshot

# Test 3: Create 1 more entity, rebuild incrementally
echo ""
echo "TEST 3: Add 1 entity + incremental rebuild"
echo "----------------------------------------------------------------------"
echo "Creating ENTITY_D..."
docker exec ipfs-api python3 -c "
import httpx
import json
import os

IPFS_API = 'http://ipfs:5001/api/v0'

# Upload metadata
metadata = {'name': 'Entity D', 'value': 1}
metadata_json = json.dumps(metadata)

response = httpx.post(
    f'{IPFS_API}/dag/put',
    params={'store-codec': 'dag-cbor', 'input-codec': 'dag-json', 'pin': 'true'},
    files={'file': ('metadata.json', metadata_json.encode(), 'application/json')}
)
response.raise_for_status()
metadata_cid = response.json()['Cid']['/']
print(f'Metadata CID: {metadata_cid}')

# Create manifest
manifest = {
    'schema': 'arke/manifest/v1',
    'pi': 'ENTITY_D00000000000000',
    'ver': 1,
    'ts': '2025-10-22T00:00:00Z',
    'prev': None,
    'components': {'metadata': {'/': metadata_cid}},
    'note': 'Entity D v1'
}
manifest_json = json.dumps(manifest)

response = httpx.post(
    f'{IPFS_API}/dag/put',
    params={'store-codec': 'dag-cbor', 'input-codec': 'dag-json', 'pin': 'true'},
    files={'file': ('manifest.json', manifest_json.encode(), 'application/json')}
)
response.raise_for_status()
manifest_cid = response.json()['Cid']['/']
print(f'Manifest CID: {manifest_cid}')

# Write .tip file
response = httpx.post(
    f'{IPFS_API}/files/write',
    params={
        'arg': '/arke/index/EN/TI/ENTITY_D00000000000000.tip',
        'create': 'true',
        'truncate': 'true',
        'parents': 'true'
    },
    files={'file': ('tip', manifest_cid.encode(), 'text/plain')}
)
response.raise_for_status()
print('Wrote .tip file')

# Read current event head
response = httpx.post(
    f'{IPFS_API}/files/read',
    params={'arg': '/arke/index-pointer'}
)
response.raise_for_status()
pointer = response.json()
prev_event = pointer.get('event_head')

# Create event
event = {
    'schema': 'arke/event@v1',
    'pi': 'ENTITY_D00000000000000',
    'type': 'create',
    'ts': '2025-10-22T00:00:00Z',
    'prev': {'/': prev_event} if prev_event else None
}
event_json = json.dumps(event)

response = httpx.post(
    f'{IPFS_API}/dag/put',
    params={'store-codec': 'dag-json', 'input-codec': 'json', 'pin': 'true'},
    files={'file': ('event.json', event_json.encode(), 'application/json')}
)
response.raise_for_status()
event_cid = response.json()['Cid']['/']
print(f'Event CID: {event_cid}')

# Update index pointer
pointer['event_head'] = event_cid
pointer['event_count'] = pointer.get('event_count', 0) + 1
pointer['total_count'] = pointer.get('total_count', 0) + 1

response = httpx.post(
    f'{IPFS_API}/files/write',
    params={
        'arg': '/arke/index-pointer',
        'create': 'true',
        'truncate': 'true'
    },
    files={'file': ('pointer.json', json.dumps(pointer).encode(), 'application/json')}
)
response.raise_for_status()
print('Updated index pointer')
print('Entity D created successfully!')
"

echo "Building snapshot (should be incremental, process only 1 event)..."
time docker exec ipfs-api python3 -m dr.build_snapshot

echo ""
echo "======================================================================"
echo "TEST COMPLETE"
echo "======================================================================"
echo "Expected results:"
echo "- Test 1: Full traversal (no previous snapshot)"
echo "- Test 2: Skip (no new events)"
echo "- Test 3: Incremental (1 new event)"
echo "======================================================================"
