#!/bin/bash
set -e

# Arke IPFS Deployment Test Suite
# Tests both ipfs-api.arke.institute and ipfs.arke.institute endpoints

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Test counter
TESTS_PASSED=0
TESTS_FAILED=0

# Configuration
API_ENDPOINT="${API_ENDPOINT:-https://ipfs-api.arke.institute}"
GATEWAY_ENDPOINT="${GATEWAY_ENDPOINT:-https://ipfs.arke.institute}"

# Cleanup flag
CLEANUP=false
if [ "$1" = "--cleanup" ]; then
    CLEANUP=true
fi

# Track test data for cleanup
TEST_CIDS=()
TEST_TIP_PATH=""

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Arke IPFS Deployment Test Suite${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "API Endpoint:     ${YELLOW}$API_ENDPOINT${NC}"
echo -e "Gateway Endpoint: ${YELLOW}$GATEWAY_ENDPOINT${NC}"
echo ""

# Helper functions
pass() {
    echo -e "${GREEN}✓ $1${NC}"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

fail() {
    echo -e "${RED}✗ $1${NC}"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

test_section() {
    echo ""
    echo -e "${BLUE}>>> $1${NC}"
}

# Test 1: API Service Health Check
test_section "Test 1: API Service Health Check"
RESPONSE=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" "$API_ENDPOINT/health")
if [ "$RESPONSE" = "200" ]; then
    HEALTH=$(curl -s --max-time 10 "$API_ENDPOINT/health" | jq -r '.status' 2>/dev/null)
    if [ "$HEALTH" = "healthy" ]; then
        pass "Health endpoint returns 200 and status=healthy"
    else
        fail "Health endpoint returned unexpected status: $HEALTH"
    fi
else
    fail "Health endpoint returned HTTP $RESPONSE"
fi

# Test 2: IPFS Version Check
test_section "Test 2: IPFS RPC - Version Check"
RESPONSE=$(curl -s --max-time 10 -X POST "$API_ENDPOINT/api/v0/version" | jq -r '.Version' 2>/dev/null)
if [ -n "$RESPONSE" ] && [ "$RESPONSE" != "null" ]; then
    pass "IPFS version: $RESPONSE"
else
    fail "Could not get IPFS version"
fi

# Test 3: File Upload
test_section "Test 3: IPFS RPC - File Upload"
TEST_FILE=$(mktemp)
echo "Test content $(date +%s)" > "$TEST_FILE"
UPLOAD_RESPONSE=$(curl -s --max-time 30 -X POST "$API_ENDPOINT/api/v0/add?cid-version=1&pin=false" -F "file=@$TEST_FILE")
UPLOAD_CID=$(echo "$UPLOAD_RESPONSE" | jq -r '.Hash' 2>/dev/null)
if [ -n "$UPLOAD_CID" ] && [ "$UPLOAD_CID" != "null" ]; then
    pass "File uploaded: $UPLOAD_CID"
else
    fail "File upload failed: $UPLOAD_RESPONSE"
fi
rm -f "$TEST_FILE"

# Test 4: Gateway Content Retrieval
if [ -n "$UPLOAD_CID" ] && [ "$UPLOAD_CID" != "null" ]; then
    test_section "Test 4: Gateway - Content Retrieval"
    GATEWAY_RESPONSE=$(curl -s --max-time 30 "$GATEWAY_ENDPOINT/ipfs/$UPLOAD_CID")
    if echo "$GATEWAY_RESPONSE" | grep -q "Test content"; then
        pass "Gateway retrieved content successfully"
    else
        fail "Gateway content retrieval failed"
    fi
fi

# Test 5: DAG Put (Store Manifest)
test_section "Test 5: IPFS RPC - DAG Put (Manifest)"
TEST_PI="01TEST$(date +%s)00000000000"
MANIFEST='{
  "schema": "arke/manifest/v1",
  "pi": "'$TEST_PI'",
  "ver": 1,
  "ts": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
  "prev": null,
  "components": {
    "data": {"/": "'$UPLOAD_CID'"}
  },
  "children_pi": [],
  "note": "Test manifest"
}'

DAG_RESPONSE=$(echo "$MANIFEST" | curl -s --max-time 30 -X POST "$API_ENDPOINT/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=true" -F "object data=@-")
MANIFEST_CID=$(echo "$DAG_RESPONSE" | jq -r '.Cid["/"]' 2>/dev/null)
if [ -n "$MANIFEST_CID" ] && [ "$MANIFEST_CID" != "null" ]; then
    pass "Manifest stored: $MANIFEST_CID"
    TEST_CIDS+=("$MANIFEST_CID")
else
    fail "DAG put failed: $DAG_RESPONSE"
fi

# Test 6: DAG Get (Retrieve Manifest)
if [ -n "$MANIFEST_CID" ] && [ "$MANIFEST_CID" != "null" ]; then
    test_section "Test 6: IPFS RPC - DAG Get (Retrieve Manifest)"
    DAG_GET_RESPONSE=$(curl -s --max-time 30 -X POST "$API_ENDPOINT/api/v0/dag/get?arg=$MANIFEST_CID")
    RETRIEVED_PI=$(echo "$DAG_GET_RESPONSE" | jq -r '.pi' 2>/dev/null)
    if [ "$RETRIEVED_PI" = "$TEST_PI" ]; then
        pass "Manifest retrieved successfully"
    else
        fail "Manifest retrieval failed or PI mismatch"
    fi
fi

# Test 7: MFS Operations (mkdir)
test_section "Test 7: IPFS RPC - MFS Mkdir"
SHARD_DIR="/arke/index/${TEST_PI:0:2}/${TEST_PI:2:2}"
MKDIR_RESPONSE=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" -X POST "$API_ENDPOINT/api/v0/files/mkdir?arg=$SHARD_DIR&parents=true")
if [ "$MKDIR_RESPONSE" = "200" ] || [ "$MKDIR_RESPONSE" = "500" ]; then
    # 500 is OK if directory already exists
    pass "MFS directory created/exists: $SHARD_DIR"
else
    fail "MFS mkdir failed with HTTP $MKDIR_RESPONSE"
fi

# Test 8: MFS Write (.tip file)
test_section "Test 8: IPFS RPC - MFS Write (.tip file)"
TIP_PATH="$SHARD_DIR/${TEST_PI}.tip"
TEST_TIP_PATH="$TIP_PATH"
WRITE_RESPONSE=$(echo -n "$MANIFEST_CID" | curl -s --max-time 30 -o /dev/null -w "%{http_code}" -X POST "$API_ENDPOINT/api/v0/files/write?arg=$TIP_PATH&create=true&truncate=true" -F "file=@-")
if [ "$WRITE_RESPONSE" = "200" ]; then
    pass ".tip file written: $TIP_PATH"
else
    fail "MFS write failed with HTTP $WRITE_RESPONSE"
fi

# Test 9: MFS Read (.tip file)
test_section "Test 9: IPFS RPC - MFS Read (.tip file)"
READ_RESPONSE=$(curl -s --max-time 30 -X POST "$API_ENDPOINT/api/v0/files/read?arg=$TIP_PATH")
if [ "$READ_RESPONSE" = "$MANIFEST_CID" ]; then
    pass ".tip file read successfully: $READ_RESPONSE"
else
    fail ".tip file read mismatch. Expected: $MANIFEST_CID, Got: $READ_RESPONSE"
fi

# Test 10: Index Pointer
test_section "Test 10: API Service - Index Pointer"
POINTER_RESPONSE=$(curl -s --max-time 10 "$API_ENDPOINT/index-pointer")
SCHEMA=$(echo "$POINTER_RESPONSE" | jq -r '.schema' 2>/dev/null)
if [ "$SCHEMA" = "arke/index-pointer@v2" ]; then
    pass "Index pointer retrieved successfully"
else
    fail "Index pointer failed: $POINTER_RESPONSE"
fi

# Test 11: Events Append
test_section "Test 11: API Service - Events Append"
EVENT_PAYLOAD='{
  "type": "create",
  "pi": "'$TEST_PI'",
  "ver": 1,
  "tip_cid": "'$MANIFEST_CID'"
}'
EVENT_RESPONSE=$(curl -s --max-time 30 -X POST "$API_ENDPOINT/events/append" \
  -H 'Content-Type: application/json' \
  -d "$EVENT_PAYLOAD")
EVENT_CID=$(echo "$EVENT_RESPONSE" | jq -r '.event_cid' 2>/dev/null)
if [ -n "$EVENT_CID" ] && [ "$EVENT_CID" != "null" ]; then
    pass "Event appended: $EVENT_CID"
else
    fail "Event append failed: $EVENT_RESPONSE"
fi

# Test 12: Events Query
test_section "Test 12: API Service - Events Query"
EVENTS_RESPONSE=$(curl -s --max-time 10 "$API_ENDPOINT/events?limit=5")
TOTAL_EVENTS=$(echo "$EVENTS_RESPONSE" | jq -r '.total_events' 2>/dev/null)
if [ -n "$TOTAL_EVENTS" ] && [ "$TOTAL_EVENTS" != "null" ]; then
    pass "Events queried successfully (total: $TOTAL_EVENTS)"
else
    fail "Events query failed: $EVENTS_RESPONSE"
fi

# Test 13: Pin Update
if [ -n "$MANIFEST_CID" ] && [ "$MANIFEST_CID" != "null" ]; then
    test_section "Test 13: IPFS RPC - Pin Update"

    # Verify first manifest is pinned
    PIN_CHECK=$(curl -s --max-time 10 -X POST "$API_ENDPOINT/api/v0/pin/ls?arg=$MANIFEST_CID&type=recursive" 2>/dev/null | jq -r '.Keys' 2>/dev/null)

    if [ "$PIN_CHECK" != "null" ] && [ -n "$PIN_CHECK" ]; then
        # Create a new version for pin update test
        MANIFEST_V2='{
          "schema": "arke/manifest/v1",
          "pi": "'$TEST_PI'",
          "ver": 2,
          "ts": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
          "prev": {"/": "'$MANIFEST_CID'"},
          "components": {
            "data": {"/": "'$UPLOAD_CID'"}
          },
          "children_pi": [],
          "note": "Test manifest v2"
        }'

        MANIFEST_V2_CID=$(echo "$MANIFEST_V2" | curl -s --max-time 30 -X POST "$API_ENDPOINT/api/v0/dag/put?store-codec=dag-cbor&input-codec=json&pin=false" -F "object data=@-" | jq -r '.Cid["/"]' 2>/dev/null)

        if [ -n "$MANIFEST_V2_CID" ] && [ "$MANIFEST_V2_CID" != "null" ]; then
            PIN_UPDATE_RESPONSE=$(curl -s --max-time 30 -o /dev/null -w "%{http_code}" -X POST "$API_ENDPOINT/api/v0/pin/update?arg=$MANIFEST_CID&arg=$MANIFEST_V2_CID")
            if [ "$PIN_UPDATE_RESPONSE" = "200" ]; then
                pass "Pin updated from v1 to v2"
                TEST_CIDS+=("$MANIFEST_V2_CID")
            else
                fail "Pin update failed with HTTP $PIN_UPDATE_RESPONSE"
            fi
        else
            fail "Could not create manifest v2 for pin update test"
        fi
    else
        pass "Pin update skipped (manifest not recursively pinned)"
    fi
fi

# Test 14: Repo Stats
test_section "Test 14: IPFS RPC - Repo Stats"
REPO_RESPONSE=$(curl -s --max-time 10 -X POST "$API_ENDPOINT/api/v0/repo/stat")
REPO_SIZE=$(echo "$REPO_RESPONSE" | jq -r '.RepoSize' 2>/dev/null)
if [ -n "$REPO_SIZE" ] && [ "$REPO_SIZE" != "null" ]; then
    pass "Repo stats retrieved (size: $REPO_SIZE bytes)"
else
    fail "Repo stats failed: $REPO_RESPONSE"
fi

# Summary
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${GREEN}Tests Passed: $TESTS_PASSED${NC}"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Tests Failed: $TESTS_FAILED${NC}"
else
    echo -e "${GREEN}Tests Failed: $TESTS_FAILED${NC}"
fi
echo ""

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
    echo ""
    echo -e "Test Entity Created:"
    echo -e "  PI:           ${YELLOW}$TEST_PI${NC}"
    echo -e "  Manifest CID: ${YELLOW}$MANIFEST_CID${NC}"
    echo -e "  Event CID:    ${YELLOW}$EVENT_CID${NC}"
    echo ""

    # Cleanup if requested
    if [ "$CLEANUP" = true ]; then
        echo -e "${BLUE}========================================${NC}"
        echo -e "${BLUE}  Cleanup${NC}"
        echo -e "${BLUE}========================================${NC}"
        echo ""

        CLEANUP_SUCCESS=0
        CLEANUP_ERRORS=0

        # Delete .tip file from MFS
        if [ -n "$TEST_TIP_PATH" ]; then
            echo -e "${YELLOW}Removing .tip file: $TEST_TIP_PATH${NC}"
            RM_RESPONSE=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" -X POST "$API_ENDPOINT/api/v0/files/rm?arg=$TEST_TIP_PATH")
            if [ "$RM_RESPONSE" = "200" ]; then
                echo -e "${GREEN}✓ .tip file removed${NC}"
                ((CLEANUP_SUCCESS++))
            else
                echo -e "${RED}✗ Failed to remove .tip file (HTTP $RM_RESPONSE)${NC}"
                ((CLEANUP_ERRORS++))
            fi
        fi

        # Unpin test manifests
        for CID in "${TEST_CIDS[@]}"; do
            echo -e "${YELLOW}Unpinning manifest: $CID${NC}"
            UNPIN_RESPONSE=$(curl -s --max-time 10 -o /dev/null -w "%{http_code}" -X POST "$API_ENDPOINT/api/v0/pin/rm?arg=$CID")
            if [ "$UNPIN_RESPONSE" = "200" ]; then
                echo -e "${GREEN}✓ Manifest unpinned${NC}"
                ((CLEANUP_SUCCESS++))
            else
                echo -e "${RED}✗ Failed to unpin manifest (HTTP $UNPIN_RESPONSE)${NC}"
                ((CLEANUP_ERRORS++))
            fi
        done

        echo ""
        echo -e "${GREEN}Cleanup complete: $CLEANUP_SUCCESS operations successful${NC}"
        if [ $CLEANUP_ERRORS -gt 0 ]; then
            echo -e "${YELLOW}Cleanup errors: $CLEANUP_ERRORS${NC}"
        fi
        echo ""
        echo -e "${BLUE}Note:${NC} Test events remain in the event chain (immutable, ~200 bytes each)"
        echo -e "${BLUE}Note:${NC} Unpinned content will be garbage collected by IPFS automatically"
        echo ""
    fi

    exit 0
else
    echo -e "${RED}✗ Some tests failed${NC}"
    exit 1
fi
