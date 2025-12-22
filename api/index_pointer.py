import httpx
from datetime import datetime, timezone
from config import settings
from models import IndexPointer
import json

# Explicit timeout configuration to prevent hanging on dead connections
_default_timeout = httpx.Timeout(
    connect=5.0,    # Connection establishment
    read=10.0,      # Reading response
    write=10.0,     # Writing request
    pool=5.0        # Waiting for connection from pool
)

async def get_index_pointer() -> IndexPointer:
    """Read index pointer from MFS."""
    try:
        # Try to read from MFS
        async with httpx.AsyncClient(timeout=_default_timeout) as client:
            response = await client.post(
                f"{settings.IPFS_API_URL}/files/read",
                params={"arg": settings.INDEX_POINTER_PATH},
            )
            response.raise_for_status()
            data = response.json()
            return IndexPointer(**data)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 500:  # File doesn't exist
            # Initialize empty index pointer (v2 schema)
            return IndexPointer(
                event_head=None,
                event_count=0,
                latest_snapshot_cid=None,
                snapshot_event_cid=None,
                snapshot_seq=0,
                snapshot_count=0,
                snapshot_ts=None,
                total_count=0,
                last_snapshot_trigger=None,
                last_updated=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            )
        raise

async def update_index_pointer(pointer: IndexPointer, timeout: float = 30.0):
    """Write index pointer to MFS.

    Args:
        pointer: IndexPointer to write
        timeout: HTTP read timeout in seconds (default 30s, increase for large operations)
    """
    pointer.last_updated = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    # Convert to JSON
    data = pointer.model_dump_json()

    # Use explicit timeout with connection limits
    write_timeout = httpx.Timeout(
        connect=5.0,
        read=timeout,
        write=timeout,
        pool=5.0
    )

    async with httpx.AsyncClient(timeout=write_timeout) as client:
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
        )
        response.raise_for_status()
