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
    last_snapshot_trigger: Optional[str] = None  # Timestamp when snapshot was last triggered (prevents rapid re-triggers)
    last_updated: str

class ChainEntry(BaseModel):
    schema: str = "arke/chain-entry@v0"
    pi: str
    ts: str
    prev: Optional[dict] = None  # IPLD link: {"/": "cid"} or null

class Snapshot(BaseModel):
    schema: str = "arke/snapshot@v0"
    seq: int
    ts: str
    prev_snapshot: Optional[dict] = None  # Link to previous snapshot
    total_count: int
    entries: list[dict]  # Direct array, no chunking

class EntitiesResponse(BaseModel):
    items: list[dict]
    total_count: int
    has_more: bool
    next_cursor: Optional[str] = None

class AppendChainRequest(BaseModel):
    pi: str
