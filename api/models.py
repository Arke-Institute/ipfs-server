from pydantic import BaseModel
from typing import Optional

class IndexPointer(BaseModel):
    schema: str = "arke/index-pointer@v1"
    latest_snapshot_cid: Optional[str] = None
    snapshot_seq: int = 0
    snapshot_count: int = 0
    snapshot_ts: Optional[str] = None
    recent_chain_head: Optional[str] = None
    recent_count: int = 0
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
    schema: str = "arke/snapshot-chunk@v1"
    chunk_index: int
    entries: list[dict]

class Snapshot(BaseModel):
    schema: str = "arke/snapshot@v2"
    seq: int
    ts: str
    prev_snapshot: Optional[dict] = None
    total_count: int
    chunk_size: int
    chunks: list[dict]  # List of IPLD links

class EntitiesResponse(BaseModel):
    items: list[dict]
    total_count: int
    has_more: bool
    next_cursor: Optional[str] = None

class AppendChainRequest(BaseModel):
    pi: str
    tip_cid: str
    ver: int
