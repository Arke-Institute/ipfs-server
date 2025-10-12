import httpx
from datetime import datetime, timezone
from config import settings
from models import IndexPointer
import json

async def get_index_pointer() -> IndexPointer:
    """Read index pointer from MFS."""
    try:
        # Try to read from MFS
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.IPFS_API_URL}/files/read",
                params={"arg": settings.INDEX_POINTER_PATH},
                timeout=5.0
            )
            response.raise_for_status()
            data = response.json()
            return IndexPointer(**data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 500:  # File doesn't exist
            # Initialize empty index pointer
            return IndexPointer(
                latest_snapshot_cid=None,
                snapshot_seq=0,
                snapshot_count=0,
                snapshot_ts=None,
                recent_chain_head=None,
                recent_count=0,
                total_count=0,
                last_snapshot_trigger=None,
                last_updated=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            )
        raise

async def update_index_pointer(pointer: IndexPointer):
    """Write index pointer to MFS."""
    pointer.last_updated = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    # Convert to JSON
    data = pointer.model_dump_json()

    async with httpx.AsyncClient() as client:
        # Write to MFS
        response = await client.post(
            f"{settings.IPFS_API_URL}/files/write",
            params={
                "arg": settings.INDEX_POINTER_PATH,
                "create": "true",
                "truncate": "true",
                "parents": "true"
            },
            files={"file": ("pointer.json", data.encode(), "application/json")},
            timeout=10.0
        )
        response.raise_for_status()
