from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import index_pointer
import snapshot
import chain
from models import EntitiesResponse, AppendChainRequest

app = FastAPI(title="Arke IPFS Index API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/entities", response_model=EntitiesResponse)
async def list_entities(
    limit: int = 10,
    offset: int = 0,
    cursor: str | None = None
):
    """
    List entities with pagination support.

    - Recent items (offset < recent_count): Query recent chain
    - Historical items: Query snapshot chunks
    - Cursor-based: Walk chain from cursor
    """
    try:
        if cursor:
            # Cursor-based pagination
            items, next_cursor = await chain.query_chain(limit=limit, cursor=cursor)
            pointer = await index_pointer.get_index_pointer()
            return EntitiesResponse(
                items=items,
                total_count=pointer.total_count,
                has_more=next_cursor is not None,
                next_cursor=next_cursor
            )
        else:
            # Offset-based pagination
            result = await snapshot.query_entities(offset=offset, limit=limit)
            return EntitiesResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/index-pointer")
async def get_pointer():
    """Get current index pointer."""
    try:
        return await index_pointer.get_index_pointer()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/chain/append")
async def append_chain(request: AppendChainRequest):
    """
    Append new entry to recent chain.
    Called by API wrapper after entity creation.
    """
    try:
        cid = await chain.append_to_chain(request.pi, request.tip_cid, request.ver)
        return {"cid": cid, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/snapshot/rebuild")
async def rebuild_snapshot():
    """
    Manually trigger snapshot rebuild.
    Walks recent chain, merges with old snapshot, creates new chunked snapshot.
    """
    # This will be implemented via the shell script
    # For now, return a placeholder
    return {"message": "Snapshot rebuild should be triggered via build-snapshot.sh script"}
