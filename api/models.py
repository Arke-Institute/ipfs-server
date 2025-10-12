from pydantic import BaseModel, ConfigDict
from typing import Optional

class IndexPointer(BaseModel):
    model_config = ConfigDict(extra='allow')  # Allow extra fields from JSON

    schema: str = "arke/index-pointer@v1"
    latest_snapshot_cid: Optional[str] = None
    snapshot_seq: int = 0
    snapshot_count: int = 0
    snapshot_ts: Optional[str] = None
    recent_chain_head: Optional[str] = None  # Always points to the latest entity (never reset to null)
    recent_count: int = 0  # Number of new entities since last snapshot
    total_count: int = 0
    last_updated: str

class ChainEntry(BaseModel):
    schema: str = "arke/chain-entry@v1"
    pi: str
    ver: int
    tip: dict  # IPLD link: {"/": "cid"}
    ts: str
    prev: Optional[dict] = None  # IPLD link or null

class SnapshotChunk(BaseModel):
    schema: str = "arke/snapshot-chunk@v2"
    chunk_index: int
    entries: list[dict]
    prev: Optional[dict] = None  # IPLD link to previous chunk (linked list)

class Snapshot(BaseModel):
    schema: str = "arke/snapshot@v3"
    seq: int
    ts: str
    prev_snapshot: Optional[dict] = None  # Link to previous snapshot
    total_count: int
    chunk_size: int
    entries_head: Optional[dict] = None  # IPLD link to head of chunk linked list

class EntitiesResponse(BaseModel):
    items: list[dict]
    total_count: int
    has_more: bool
    next_cursor: Optional[str] = None

class AppendChainRequest(BaseModel):
    pi: str
    tip_cid: str
    ver: int
