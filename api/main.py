from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import index_pointer
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
    cursor: str | None = None
):
    """
    List entities with cursor-based pagination.

    - If no cursor provided: starts from recent_chain_head (most recent entities)
    - If cursor provided: continues from that CID
    - Walks the chain backwards via prev links: O(limit) always
    """
    try:
        pointer = await index_pointer.get_index_pointer()

        # Start from cursor or head of chain
        start_cid = cursor or pointer.recent_chain_head

        if not start_cid:
            # Empty chain
            return EntitiesResponse(
                items=[],
                total_count=0,
                has_more=False,
                next_cursor=None
            )

        # Walk chain from start_cid
        items, next_cursor = await chain.query_chain(limit=limit, cursor=start_cid)

        return EntitiesResponse(
            items=items,
            total_count=pointer.total_count,
            has_more=next_cursor is not None,
            next_cursor=next_cursor
        )
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
    Append new PI to recent chain.
    Called by API wrapper after entity creation.
    Chain only stores PI + timestamp - tip/version info is in MFS.
    """
    try:
        cid = await chain.append_to_chain(request.pi)
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
