import httpx
from datetime import datetime, timezone
from config import settings
from models import Event, IndexPointer
import index_pointer
import json
import asyncio
import subprocess
import os
from pathlib import Path

# Global lock to prevent race conditions during event append operations
_event_append_lock = asyncio.Lock()

async def append_event(event_type: str, pi: str, ver: int, tip_cid: str) -> str:
    """
    Append new event to the event chain.

    Args:
        event_type: "create" or "update"
        pi: Persistent identifier (ULID)
        ver: Version number (from manifest)
        tip_cid: Manifest CID

    Returns:
        Event CID

    Uses a lock to prevent race conditions when multiple concurrent requests
    try to append to the event chain simultaneously.
    """
    async with _event_append_lock:
        # 1. Get current index pointer
        pointer = await index_pointer.get_index_pointer()

        # 2. Create new event
        event = Event(
            type=event_type,
            pi=pi,
            ver=ver,
            tip_cid={"/": tip_cid},
            ts=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            prev={"/": pointer.event_head} if pointer.event_head else None
        )

        # 3. Store as DAG-CBOR (more efficient and works with HTTP API dag/get)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.IPFS_API_URL}/dag/put",
                params={
                    "store-codec": "dag-cbor",
                    "input-codec": "json",
                    "pin": "true"
                },
                files={"file": ("event.json", event.model_dump_json().encode(), "application/json")},
                timeout=10.0
            )
            response.raise_for_status()

            # Parse the response to get the CID
            result_text = response.text.strip()
            result = json.loads(result_text)
            new_cid = result["Cid"]["/"]

        # 4. Update index pointer
        pointer.event_head = new_cid
        pointer.event_count += 1

        # Update total_count only for create events
        if event_type == "create":
            pointer.total_count += 1

        await index_pointer.update_index_pointer(pointer)

        return new_cid

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

    # Path to build-snapshot.sh script (inside container: /app/scripts/)
    script_path = "/app/scripts/build-snapshot.sh"
    log_path = "/app/logs/snapshot-build.log"

    # Ensure logs directory exists
    Path("/app/logs").mkdir(exist_ok=True)

    # Update trigger timestamp
    trigger_time = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    pointer.last_snapshot_trigger = trigger_time
    await index_pointer.update_index_pointer(pointer)

    # Trigger snapshot build in background (fire-and-forget)
    try:
        with open(log_path, 'a') as log_file:
            log_file.write(f"\n{'='*60}\n")
            log_file.write(f"[SCHEDULED] Snapshot build triggered at {trigger_time}\n")
            log_file.write(f"Total PIs: {pointer.total_count}\n")
            log_file.write(f"Total events: {pointer.event_count}\n")
            log_file.write(f"{'='*60}\n\n")

            subprocess.Popen(
                [script_path],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd="/app",
                env={
                    **os.environ,
                    "CONTAINER_NAME": "ipfs-node",
                    "IPFS_API_URL": settings.IPFS_API_URL
                }
            )
        print(f"✅ Snapshot build triggered in background (logging to {log_path})")
    except Exception as e:
        print(f"❌ Failed to trigger snapshot build: {e}")
