import httpx
from datetime import datetime, timezone
from config import settings
from models import ChainEntry, IndexPointer
import index_pointer
import json
import subprocess
import os
from pathlib import Path
import asyncio

# Global lock to prevent race conditions during chain append operations
_chain_append_lock = asyncio.Lock()

async def append_to_chain(pi: str) -> str:
    """
    Append a new PI to the recent chain.
    Chain is just an ordered list of PIs - tip/version info is in MFS.
    Returns the new chain entry CID.

    Uses a lock to prevent race conditions when multiple concurrent requests
    try to append to the chain simultaneously.
    """
    async with _chain_append_lock:
        # 1. Get current index pointer
        pointer = await index_pointer.get_index_pointer()

        # 2. Create new chain entry (just PI + timestamp + prev link)
        entry = ChainEntry(
            pi=pi,
            ts=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            prev={"/": pointer.recent_chain_head} if pointer.recent_chain_head else None
        )

        # 3. Store as DAG-JSON
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.IPFS_API_URL}/dag/put",
                params={
                    "store-codec": "dag-json",
                    "input-codec": "json",
                    "pin": "true"
                },
                files={"file": ("entry.json", entry.model_dump_json().encode(), "application/json")},
                timeout=10.0
            )
            response.raise_for_status()

            # Parse the response to get the CID
            result_text = response.text.strip()
            # The response is a JSON object with Cid field
            result = json.loads(result_text)
            new_cid = result["Cid"]["/"]

        # 4. Update index pointer
        pointer.recent_chain_head = new_cid
        pointer.recent_count += 1
        pointer.total_count += 1
        await index_pointer.update_index_pointer(pointer)

        return new_cid

async def query_chain(limit: int = 10, cursor: str | None = None) -> tuple[list[dict], str | None]:
    """
    Walk the recent chain and return up to `limit` items.
    For each PI, reads the current tip from MFS to get latest version info.
    Returns (items, next_cursor).
    """
    pointer = await index_pointer.get_index_pointer()

    # Start from cursor or head
    current_cid = cursor or pointer.recent_chain_head

    if not current_cid:
        return [], None

    items = []

    async with httpx.AsyncClient() as client:
        for _ in range(limit):
            # Fetch chain entry (just PI + timestamp)
            response = await client.post(
                f"{settings.IPFS_API_URL}/dag/get",
                params={"arg": current_cid},
                timeout=5.0
            )
            response.raise_for_status()
            entry_data = response.json()

            pi = entry_data["pi"]

            # Read current tip from MFS to get latest manifest CID
            tip_response = await client.post(
                f"{settings.IPFS_API_URL}/files/read",
                params={"arg": f"/arke/index/{pi[:2]}/{pi[2:4]}/{pi}.tip"},
                timeout=5.0
            )
            tip_response.raise_for_status()
            tip_cid = tip_response.text.strip()

            # Fetch manifest to get version number
            manifest_response = await client.post(
                f"{settings.IPFS_API_URL}/dag/get",
                params={"arg": tip_cid},
                timeout=5.0
            )
            manifest_response.raise_for_status()
            manifest = manifest_response.json()

            # Add to results with current tip info (read from MFS, always fresh)
            items.append({
                "pi": pi,
                "ver": manifest["ver"],
                "tip": tip_cid,
                "ts": entry_data["ts"]  # Timestamp from chain entry (when PI was created)
            })

            # Move to previous
            if not entry_data.get("prev"):
                # End of chain
                return items, None

            current_cid = entry_data["prev"]["/"]

        # More items available
        return items, current_cid

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

    print(f"⏰ Scheduled snapshot trigger (total: {pointer.total_count}, recent: {pointer.recent_count})")

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
            log_file.write(f"Total entities: {pointer.total_count}\n")
            log_file.write(f"Recent count: {pointer.recent_count}\n")
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
