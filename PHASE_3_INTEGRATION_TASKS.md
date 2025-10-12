# Phase 3: API Wrapper Integration Tasks

**Status:** Ready for implementation
**Estimated Time:** 5-6 hours

The IPFS Server backend is complete and tested. These are the integration tasks needed in the API wrapper (ipfs_wrapper/Cloudflare Workers) to complete the migration.

---

## Available IPFS Server API Endpoints

The following endpoints are ready and tested:

### `GET /health`
Health check endpoint
- Returns: `{"status": "healthy"}`

### `GET /index-pointer`
Get current system state
- Returns: IndexPointer object with counts and CIDs

### `GET /entities`
List entities with pagination
- Query params: `limit` (default: 10), `offset` (default: 0), `cursor` (optional)
- Returns: `{items: [...], total_count, has_more, next_cursor}`
- Handles hybrid queries (recent chain + snapshot)

### `POST /chain/append`
Append new entity to recent chain
- Body: `{pi: string, tip_cid: string, ver: number}`
- Returns: `{cid: string, success: boolean}`

---

## Task 3.1: Configure IPFS Server Backend URL

**Priority:** Critical
**Estimated Time:** 15 minutes

**What Needs to Happen:**
- Add environment variable for IPFS Server API URL
- Make configurable per environment (dev, production)
- Ensure URL is accessible from Cloudflare Worker runtime

**Required Environment Variable:**
```
IPFS_SERVER_API_URL=http://localhost:3000
```

**Acceptance Criteria:**
- [ ] Environment variable configured in all environments
- [ ] Variable accessible in Worker code
- [ ] Can successfully ping `/health` endpoint

---

## Task 3.2: Integrate Chain Append on Entity Creation

**Priority:** Critical
**Estimated Time:** 2 hours

**What Needs to Happen:**

When creating a new entity (after successfully storing manifest and writing .tip file), make an HTTP call to append the entity to the recent chain.

**API Call Required:**
```http
POST {IPFS_SERVER_API_URL}/chain/append
Content-Type: application/json

{
  "pi": "01K7...",
  "tip_cid": "baguqee...",
  "ver": 1
}
```

**Important Notes:**
- Call this AFTER manifest is stored and .tip file is written (don't call if entity creation fails)
- This is an optimization - if the chain append fails, log it but don't fail the entity creation request
- Handle errors gracefully (network timeout, 500 errors, etc.)
- The chain append happens asynchronously - no need to wait for it to complete

**Acceptance Criteria:**
- [ ] Chain append called after successful entity creation
- [ ] Errors logged but don't break entity creation
- [ ] Can verify entity appears in `/entities` endpoint
- [ ] Index pointer `recent_count` increments

---

## Task 3.3: Replace Entity Listing with Backend API Call

**Priority:** Critical
**Estimated Time:** 2-3 hours

**What Needs to Happen:**

Remove all MFS directory traversal code and replace with a simple HTTP call to the IPFS Server backend.

**API Call Required:**
```http
GET {IPFS_SERVER_API_URL}/entities?limit=10&offset=0
```

**What to Remove:**
- All MFS `/files/ls` traversal logic
- Manual .tip file reading
- Manual sorting and pagination logic
- Any cursor-based pagination implementation (backend handles this now)

**What to Keep:**
- Manifest fetching (via IPFS dag/get) - still needed to return full entity data
- Error handling and response formatting

**Response Format from Backend:**
```json
{
  "items": [
    {"pi": "01K7...", "ver": 1, "tip": "baguqee...", "ts": "2025-10-11T..."}
  ],
  "total_count": 5,
  "has_more": false,
  "next_cursor": null
}
```

**Implementation Pattern:**
1. Parse query params (`limit`, `offset`) from incoming request
2. Call backend API with these params
3. For each item in response, fetch the manifest using existing dag/get logic
4. Return combined data to client

**Acceptance Criteria:**
- [ ] No MFS traversal code remains
- [ ] Entity listing works for offset-based pagination
- [ ] Performance: Latest 10 entities return in < 200ms
- [ ] Performance: Deep pagination (offset=5000) returns in < 500ms
- [ ] All existing API contracts maintained (backward compatible)

---

## Task 3.4: Testing & Validation

**Priority:** Critical
**Estimated Time:** 1-2 hours

**What to Test:**

### Entity Creation Flow
1. Create a new entity
2. Verify it appears in `/entities` immediately
3. Verify `recent_count` in `/index-pointer` increments
4. Verify entity is queryable

### Entity Listing
1. List entities with default pagination
2. List entities with custom limit/offset
3. Verify performance benchmarks:
   - Latest 10: < 200ms
   - Offset 5000: < 500ms
4. Verify backward compatibility with existing clients

### Error Handling
1. Test with backend API down (should gracefully fail)
2. Test with network timeout
3. Test with invalid pagination params
4. Verify appropriate error messages returned

**Acceptance Criteria:**
- [ ] All integration tests pass
- [ ] Performance benchmarks met
- [ ] Error handling verified
- [ ] No regression in existing functionality

---

## Data Structures Reference

### Index Pointer Response
```json
{
  "schema": "arke/index-pointer@v1",
  "latest_snapshot_cid": "baguqee...",
  "snapshot_seq": 1,
  "snapshot_count": 5,
  "snapshot_ts": "2025-10-11T18:44:31Z",
  "recent_chain_head": "baguqee...",
  "recent_count": 0,
  "total_count": 5,
  "last_updated": "2025-10-11T18:44:31Z"
}
```

### Chain Entry (for reference)
```json
{
  "schema": "arke/chain-entry@v1",
  "pi": "01K7...",
  "ver": 1,
  "tip": {"/": "baguqee..."},
  "ts": "2025-10-11T12:30:15Z",
  "prev": {"/": "baguqee..."}
}
```

---

## Success Criteria

✅ **Phase 3 Complete When:**
- Entity creation appends to recent chain
- Entity listing queries backend API (no MFS traversal)
- Response times meet performance targets:
  - Latest 10: < 200ms
  - Deep pagination (offset=5000): < 500ms
- All integration tests pass
- Backward compatibility maintained
- Error handling robust

---

## Rollback Plan

If issues arise:

1. **Revert deployment** - Roll back to previous version
2. **Backend still works** - IPFS Server API continues to function
3. **Old code still functional** - MFS .tip files are still maintained
4. **No data loss** - All data remains accessible

The hybrid system is designed to be backward compatible - rolling back the API wrapper won't break anything.
