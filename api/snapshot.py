import httpx
from config import settings
from models import Snapshot, SnapshotChunk
import index_pointer

async def query_snapshot(offset: int, limit: int) -> tuple[list[dict], bool]:
    """
    Query snapshot with offset/limit pagination.
    Uses chunked snapshots for efficient access.

    Returns (items, has_more).
    """
    pointer = await index_pointer.get_index_pointer()

    if not pointer.latest_snapshot_cid:
        return [], False

    # Fetch snapshot metadata
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.IPFS_API_URL}/dag/get",
            params={"arg": pointer.latest_snapshot_cid},
            timeout=10.0
        )
        response.raise_for_status()
        snapshot_data = response.json()

    snapshot = Snapshot(**snapshot_data)

    # Calculate which chunk(s) to fetch
    start_chunk_idx = offset // snapshot.chunk_size
    end_chunk_idx = (offset + limit - 1) // snapshot.chunk_size

    # Offset within first chunk
    offset_in_chunk = offset % snapshot.chunk_size

    items = []

    async with httpx.AsyncClient() as client:
        for chunk_idx in range(start_chunk_idx, end_chunk_idx + 1):
            if chunk_idx >= len(snapshot.chunks):
                break

            # Fetch chunk
            chunk_cid = snapshot.chunks[chunk_idx]["/"]
            response = await client.post(
                f"{settings.IPFS_API_URL}/dag/get",
                params={"arg": chunk_cid},
                timeout=10.0
            )
            response.raise_for_status()
            chunk_data = response.json()

            chunk = SnapshotChunk(**chunk_data)

            # Extract relevant entries
            if chunk_idx == start_chunk_idx:
                # First chunk: start from offset
                chunk_items = chunk.entries[offset_in_chunk:]
            else:
                # Subsequent chunks: take all
                chunk_items = chunk.entries

            # Add to results
            for entry in chunk_items:
                if len(items) >= limit:
                    break
                items.append({
                    "pi": entry["pi"],
                    "ver": entry["ver"],
                    "tip": entry["tip"]["/"],
                    "ts": entry["ts"]
                })

            if len(items) >= limit:
                break

    has_more = (offset + limit) < snapshot.total_count

    return items, has_more

async def query_entities(offset: int, limit: int) -> dict:
    """
    Combined query that handles both recent chain and snapshot.

    Strategy:
    - If offset < recent_count: Query recent chain
    - If offset >= recent_count: Query snapshot (offset adjusted)
    """
    pointer = await index_pointer.get_index_pointer()

    if offset < pointer.recent_count:
        # Query recent chain
        import chain

        # Walk chain to offset
        skip_count = offset
        items_needed = limit

        current_cid = pointer.recent_chain_head
        all_items = []

        async with httpx.AsyncClient() as client:
            while current_cid and len(all_items) < offset + limit:
                response = await client.post(
                    f"{settings.IPFS_API_URL}/dag/get",
                    params={"arg": current_cid},
                    timeout=5.0
                )
                response.raise_for_status()
                entry_data = response.json()

                all_items.append({
                    "pi": entry_data["pi"],
                    "ver": entry_data["ver"],
                    "tip": entry_data["tip"]["/"],
                    "ts": entry_data["ts"]
                })

                if not entry_data.get("prev"):
                    break
                current_cid = entry_data["prev"]["/"]

        # Slice to get requested range
        items = all_items[offset:offset + limit]
        has_more = len(all_items) > offset + limit or offset + limit < pointer.total_count

        return {
            "items": items,
            "total_count": pointer.total_count,
            "has_more": has_more,
            "next_cursor": None  # Could implement cursor-based pagination
        }
    else:
        # Query snapshot (adjust offset)
        snapshot_offset = offset - pointer.recent_count
        items, has_more = await query_snapshot(snapshot_offset, limit)

        return {
            "items": items,
            "total_count": pointer.total_count,
            "has_more": has_more,
            "next_cursor": None
        }
