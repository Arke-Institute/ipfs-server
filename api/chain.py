import httpx
from datetime import datetime, timezone
from config import settings
from models import ChainEntry, IndexPointer
import index_pointer
import json
import subprocess
import os
from pathlib import Path

async def append_to_chain(pi: str) -> str:
    """
    Append a new PI to the recent chain.
    Chain is just an ordered list of PIs - tip/version info is in MFS.
    Returns the new chain entry CID.
    """
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

    # 5. Check if rebuild needed and auto-trigger
    if pointer.recent_count >= settings.REBUILD_THRESHOLD:
        print(f"⚠️  Threshold reached: {pointer.recent_count}/{settings.REBUILD_THRESHOLD} entities")

        if settings.AUTO_SNAPSHOT:
            print("🔄 Triggering automatic snapshot build...")

            # Path to build-snapshot.sh script (inside container: /app/scripts/)
            script_path = "/app/scripts/build-snapshot.sh"

            # Trigger snapshot build in background (fire-and-forget)
            try:
                subprocess.Popen(
                    [script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd="/app",
                    env={
                        **os.environ,
                        "CONTAINER_NAME": "ipfs-node",
                        "IPFS_API_URL": settings.IPFS_API_URL  # Pass the correct API URL for internal docker networking
                    }
                )
                print("✅ Snapshot build triggered in background")
            except Exception as e:
                print(f"❌ Failed to trigger snapshot build: {e}")

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
