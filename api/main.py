from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from config import settings
import index_pointer
import events
import event_queue
from models import AppendEventRequest
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

@app.get("/events")
async def list_events(
    limit: int = 50,
    cursor: str | None = None
):
    """
    List events with cursor-based pagination.

    Returns time-ordered log of create/update events.
    Mirrors should poll this endpoint for incremental updates.

    - If no cursor: starts from event_head (most recent)
    - If cursor provided: continues from that event CID
    - Walks chain backwards via prev links

    Response includes:
    - items: List of events [{event_cid, type, pi, ver, tip_cid, ts}]
    - total_events: Total count of events
    - total_pis: Total count of unique PIs
    - has_more: Boolean
    - next_cursor: Event CID for next page (or null)
    """
    try:
        pointer = await index_pointer.get_index_pointer()

        # Walk event chain
        items, next_cursor = await events.query_events(limit=limit, cursor=cursor)

        return {
            "items": items,
            "total_events": pointer.event_count,
            "total_pis": pointer.total_count,
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor
        }
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

@app.post("/events/append")
async def append_event(request: AppendEventRequest):
    """
    Queue an event for appending to the chain.
    Called by API wrapper after entity creation/update.

    Request body:
    - type: "create" | "update"
    - pi: Persistent identifier
    - ver: Version number
    - tip_cid: Manifest CID

    Returns immediately with {"queued": true, "success": true}.
    Events are processed in batches by a background worker.
    """
    try:
        result = await events.append_event(
            event_type=request.type,
            pi=request.pi,
            ver=request.ver,
            tip_cid=request.tip_cid
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/events/queue-stats")
async def get_queue_stats():
    """Get current event queue statistics for monitoring."""
    return events.get_queue_stats()

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
    """Initialize event queue worker and scheduler on startup."""
    # Start event queue worker
    await event_queue.start_worker()

    # Start snapshot scheduler
    if settings.AUTO_SNAPSHOT:
        interval = settings.SNAPSHOT_INTERVAL_MINUTES
        print(f"🕐 Starting snapshot scheduler (every {interval} minutes)")

        scheduler.add_job(
            events.trigger_scheduled_snapshot,
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
    """Shutdown event queue worker and scheduler."""
    # Stop event queue worker (flushes remaining events)
    await event_queue.stop_worker()

    # Stop scheduler
    if scheduler.running:
        scheduler.shutdown()
        print("🛑 Snapshot scheduler stopped")
