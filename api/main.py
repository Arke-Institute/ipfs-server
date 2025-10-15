from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import settings
import index_pointer
import chain
from models import EntitiesResponse, AppendChainRequest
import httpx

app = FastAPI(title="Arke IPFS Index API", version="1.0.0")

# Scheduler for time-based snapshot triggers
scheduler = AsyncIOScheduler()

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

@app.get("/snapshot/latest")
async def get_latest_snapshot():
    """
    Get the latest snapshot as streaming JSON.

    Returns the complete snapshot object which includes:
    - schema, seq, ts: Snapshot metadata
    - total_count: Total number of entities
    - entries[]: Array of {pi, ver, tip_cid, ts, chain_cid} objects
    - prev_snapshot: Link to previous snapshot

    This endpoint is designed for bulk mirror access - clients can download
    the entire snapshot to get all historical PIs without walking the chain.
    Uses streaming to handle large snapshots efficiently.
    """
    try:
        # Get index pointer to find latest snapshot
        pointer = await index_pointer.get_index_pointer()

        if not pointer.latest_snapshot_cid:
            raise HTTPException(
                status_code=404,
                detail="No snapshot available yet. Create entities and trigger a snapshot first."
            )

        # Stream the snapshot from Kubo
        async def stream_snapshot():
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{settings.IPFS_API_URL}/dag/get",
                    params={"arg": pointer.latest_snapshot_cid},
                    timeout=settings.SNAPSHOT_TIMEOUT_SECONDS
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        yield chunk

        return StreamingResponse(
            stream_snapshot(),
            media_type="application/json",
            headers={
                "X-Snapshot-CID": pointer.latest_snapshot_cid,
                "X-Snapshot-Seq": str(pointer.snapshot_seq),
                "X-Snapshot-Count": str(pointer.snapshot_count)
            }
        )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=503, detail=f"Failed to retrieve snapshot from IPFS: {str(e)}")
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

@app.on_event("startup")
async def startup_event():
    """Initialize scheduler on startup."""
    if settings.AUTO_SNAPSHOT:
        interval = settings.SNAPSHOT_INTERVAL_MINUTES
        print(f"🕐 Starting snapshot scheduler (every {interval} minutes)")

        scheduler.add_job(
            chain.trigger_scheduled_snapshot,
            'interval',
            minutes=interval,
            id='snapshot_builder',
            replace_existing=True
        )
        scheduler.start()
        print(f"✅ Snapshot scheduler started")
    else:
        print("ℹ️  Auto-snapshot disabled (AUTO_SNAPSHOT=false)")

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown scheduler on app shutdown."""
    if scheduler.running:
        scheduler.shutdown()
        print("🛑 Snapshot scheduler stopped")
