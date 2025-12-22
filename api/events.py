"""
Event management for the Arke event chain.

Events are appended via an in-memory queue for immediate response,
then batch-processed by a background worker.
"""

import httpx
from datetime import datetime, timezone
from config import settings
import index_pointer
import asyncio
import subprocess
from pathlib import Path

# Import queue functions
import event_queue


async def append_event(event_type: str, pi: str, ver: int, tip_cid: str) -> dict:
    """
    Queue an event for appending to the event chain.

    Args:
        event_type: "create" or "update"
        pi: Persistent identifier (ULID)
        ver: Version number (from manifest)
        tip_cid: Manifest CID

    Returns:
        {"queued": True, "success": True}

    Events are queued immediately and processed in batches by a background
    worker. This prevents Cloudflare timeouts under high load.
    """
    return await event_queue.enqueue_event(event_type, pi, ver, tip_cid)


async def query_events(limit: int = 50, cursor: str | None = None) -> tuple[list[dict], str | None]:
    """
    Walk the event chain and return up to `limit` events.

    Args:
        limit: Maximum number of events to return
        cursor: Event CID to start from (or None for head)

    Returns:
        (events_list, next_cursor)

    Events are returned in reverse chronological order (newest first).
    Each event includes: event_cid, type, pi, ver, tip_cid, ts
    """
    pointer = await index_pointer.get_index_pointer()

    # Start from cursor or head
    current_cid = cursor or pointer.event_head

    if not current_cid:
        return [], None

    events = []

    async with httpx.AsyncClient() as client:
        for _ in range(limit):
            # Fetch event
            response = await client.post(
                f"{settings.IPFS_API_URL}/dag/get",
                params={"arg": current_cid},
                timeout=5.0
            )
            response.raise_for_status()
            event_data = response.json()

            # Add to results
            events.append({
                "event_cid": current_cid,
                "type": event_data["type"],
                "pi": event_data["pi"],
                "ver": event_data["ver"],
                "tip_cid": event_data["tip_cid"]["/"],
                "ts": event_data["ts"]
            })

            # Move to previous
            if not event_data.get("prev"):
                # End of chain
                return events, None

            current_cid = event_data["prev"]["/"]

        # More events available
        return events, current_cid


def get_queue_stats() -> dict:
    """Get current queue statistics for monitoring."""
    return {
        "queue_size": event_queue.get_queue_size(),
        "batch_size": event_queue.BATCH_SIZE,
        "batch_timeout_ms": event_queue.BATCH_TIMEOUT_MS
    }


async def trigger_scheduled_snapshot():
    """
    Triggered by scheduler every N minutes.
    Builds a snapshot if there are entities and no build is already in progress.
    """
    # Check if lock file exists (snapshot already building)
    lock_file = Path("/tmp/arke-snapshot.lock")
    if lock_file.exists():
        print("⏳ Snapshot build already in progress (lock file exists), skipping scheduled trigger")
        return

    # Get current state
    pointer = await index_pointer.get_index_pointer()

    # Skip if no entities exist
    if pointer.total_count == 0:
        print("ℹ️  No entities to snapshot, skipping scheduled trigger")
        return

    print(f"⏰ Scheduled snapshot trigger (total PIs: {pointer.total_count}, total events: {pointer.event_count})")

    # Update trigger timestamp
    trigger_time = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    pointer.last_snapshot_trigger = trigger_time
    # Increase timeout for large datasets (31k+ entities)
    await index_pointer.update_index_pointer(pointer, timeout=600.0)

    # Trigger snapshot build in background (fire-and-forget)
    # Run in thread pool to avoid blocking async event loop
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_snapshot_build, trigger_time, pointer.total_count, pointer.event_count)


def _run_snapshot_build(trigger_time: str, total_pis: int, total_events: int):
    """
    Run snapshot build in thread pool to avoid blocking async loop.
    This calls the DR Python script directly.
    """
    log_path = Path("/app/logs/snapshot-build.log")
    log_path.parent.mkdir(exist_ok=True)

    try:
        with open(log_path, 'a') as log_file:
            log_file.write(f"\n{'='*60}\n")
            log_file.write(f"[SCHEDULED] Snapshot build at {trigger_time}\n")
            log_file.write(f"Total PIs: {total_pis}\n")
            log_file.write(f"Total events: {total_events}\n")
            log_file.write(f"{'='*60}\n\n")

            # Run build_snapshot.py as subprocess
            subprocess.run(
                ["python3", "-m", "dr.build_snapshot"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd="/app",
                check=False  # Don't raise on error, just log
            )
        print(f"✅ Snapshot build completed (see {log_path})")
    except Exception as e:
        print(f"❌ Snapshot build failed: {e}")
