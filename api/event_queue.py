"""
Event Queue System for batched, async event processing.

Events are accepted immediately into an in-memory queue and processed
in batches by a background worker. This prevents Cloudflare timeouts
and improves throughput by batching IPFS writes.
"""

import asyncio
import httpx
from datetime import datetime, timezone
from typing import Optional
from config import settings
from models import Event
import index_pointer
import json

# Queue configuration
BATCH_SIZE = 50           # Max events per batch
BATCH_TIMEOUT_MS = 500    # Max wait before processing partial batch (ms)

# Global state
_event_queue: asyncio.Queue[dict] = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None
_shutdown_event: asyncio.Event = asyncio.Event()

# Shared HTTP client for connection pooling
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client with proper timeouts."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=30.0,
                write=30.0,
                pool=5.0
            )
        )
    return _http_client


async def start_worker():
    """Start the background event processing worker."""
    global _worker_task
    _shutdown_event.clear()
    _worker_task = asyncio.create_task(_event_worker())
    print("🚀 Event queue worker started")


async def stop_worker():
    """Stop the worker and flush remaining events."""
    global _worker_task, _http_client

    queue_size = _event_queue.qsize()
    if queue_size > 0:
        print(f"⏳ Stopping event worker, {queue_size} events in queue...")

    # Signal shutdown
    _shutdown_event.set()

    # Wait for worker to finish (with timeout)
    if _worker_task:
        try:
            await asyncio.wait_for(_worker_task, timeout=60.0)
            print("✅ Event queue worker stopped cleanly")
        except asyncio.TimeoutError:
            print(f"⚠️ Worker shutdown timeout, {_event_queue.qsize()} events may be lost")
            _worker_task.cancel()

    # Close HTTP client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


async def enqueue_event(event_type: str, pi: str, ver: int, tip_cid: str) -> dict:
    """
    Add an event to the processing queue.
    Returns immediately without waiting for IPFS write.
    """
    event_data = {
        "type": event_type,
        "pi": pi,
        "ver": ver,
        "tip_cid": tip_cid,
        "ts": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "queued_at": datetime.now(timezone.utc).isoformat()
    }

    await _event_queue.put(event_data)

    return {
        "queued": True,
        "success": True
    }


def get_queue_size() -> int:
    """Get current queue size for monitoring."""
    return _event_queue.qsize()


async def _event_worker():
    """Background worker that processes events in batches."""
    print(f"📋 Event worker running (batch_size={BATCH_SIZE}, timeout={BATCH_TIMEOUT_MS}ms)")

    while not _shutdown_event.is_set() or not _event_queue.empty():
        batch = []

        try:
            # Wait for first event (with timeout to check shutdown)
            try:
                event = await asyncio.wait_for(
                    _event_queue.get(),
                    timeout=1.0  # Check shutdown flag every second
                )
                batch.append(event)
            except asyncio.TimeoutError:
                continue  # No events, check shutdown flag

            # Collect more events up to batch size or timeout
            deadline = asyncio.get_event_loop().time() + (BATCH_TIMEOUT_MS / 1000)

            while len(batch) < BATCH_SIZE:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break

                try:
                    event = await asyncio.wait_for(
                        _event_queue.get(),
                        timeout=remaining
                    )
                    batch.append(event)
                except asyncio.TimeoutError:
                    break  # Timeout reached, process what we have

            # Process the batch
            if batch:
                await _process_batch(batch)

        except asyncio.CancelledError:
            # Process any remaining events before exiting
            if batch:
                await _process_batch(batch)
            raise
        except Exception as e:
            print(f"❌ Event worker error: {e}")
            # Mark events as done even on error to prevent queue backup
            for _ in batch:
                try:
                    _event_queue.task_done()
                except ValueError:
                    pass

    print("📋 Event worker finished")


async def _process_batch(batch: list[dict]):
    """Process a batch of events, writing them all to IPFS."""
    start_time = datetime.now(timezone.utc)

    try:
        # Get current pointer
        pointer = await index_pointer.get_index_pointer()
        current_head = pointer.event_head

        client = await _get_http_client()
        events_written = 0

        for event_data in batch:
            try:
                # Create event object with prev pointer
                event = Event(
                    type=event_data["type"],
                    pi=event_data["pi"],
                    ver=event_data["ver"],
                    tip_cid={"/": event_data["tip_cid"]},
                    ts=event_data["ts"],
                    prev={"/": current_head} if current_head else None
                )

                # Write to IPFS
                response = await client.post(
                    f"{settings.IPFS_API_URL}/dag/put",
                    params={
                        "store-codec": "dag-cbor",
                        "input-codec": "json",
                        "pin": "true"
                    },
                    files={"file": ("event.json", event.model_dump_json().encode(), "application/json")},
                )
                response.raise_for_status()

                # Get new CID
                result = json.loads(response.text.strip())
                new_cid = result["Cid"]["/"]

                # Update running state
                current_head = new_cid
                pointer.event_count += 1
                if event_data["type"] == "create":
                    pointer.total_count += 1

                events_written += 1

            except Exception as e:
                print(f"⚠️ Failed to write event (pi={event_data['pi']}): {e}")
                # Continue with next event in batch

        # Update pointer once for whole batch
        if events_written > 0:
            pointer.event_head = current_head
            await index_pointer.update_index_pointer(pointer)

        elapsed_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        print(f"✅ Batch: {events_written}/{len(batch)} events in {elapsed_ms:.0f}ms")

    except Exception as e:
        print(f"❌ Batch processing failed: {e}")

    finally:
        # Mark all events as done
        for _ in batch:
            try:
                _event_queue.task_done()
            except ValueError:
                pass
