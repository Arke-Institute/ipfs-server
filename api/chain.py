import httpx
from datetime import datetime, timezone
from config import settings
from models import ChainEntry, IndexPointer
import index_pointer
import json

async def append_to_chain(pi: str, tip_cid: str, ver: int) -> str:
    """
    Append a new entry to the recent chain.
    Returns the new chain entry CID.
    """
    # 1. Get current index pointer
    pointer = await index_pointer.get_index_pointer()

    # 2. Create new chain entry
    entry = ChainEntry(
        pi=pi,
        ver=ver,
        tip={"/": tip_cid},
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

    # 5. Check if rebuild needed
    if pointer.recent_count >= settings.REBUILD_THRESHOLD:
        # Trigger background snapshot rebuild
        # (Could use task queue, webhook, or just log warning)
        print(f"WARNING: Recent chain has {pointer.recent_count} items. Rebuild recommended.")

    return new_cid

async def query_chain(limit: int = 10, cursor: str | None = None) -> tuple[list[dict], str | None]:
    """
    Walk the recent chain and return up to `limit` items.
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
            # Fetch chain entry
            response = await client.post(
                f"{settings.IPFS_API_URL}/dag/get",
                params={"arg": current_cid},
                timeout=5.0
            )
            response.raise_for_status()
            entry_data = response.json()

            # Add to results (without the prev link for API response)
            items.append({
                "pi": entry_data["pi"],
                "ver": entry_data["ver"],
                "tip": entry_data["tip"]["/"],
                "ts": entry_data["ts"]
            })

            # Move to previous
            if not entry_data.get("prev"):
                # End of chain
                return items, None

            current_cid = entry_data["prev"]["/"]

        # More items available
        return items, current_cid
