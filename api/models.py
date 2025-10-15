from pydantic import BaseModel, ConfigDict
from typing import Optional

class IndexPointer(BaseModel):
    model_config = ConfigDict(extra='allow')  # Allow extra fields from JSON

    schema: str = "arke/index-pointer@v2"

    # Event chain fields
    event_head: Optional[str] = None  # Head of event chain (most recent event)
    event_count: int = 0  # Total number of events

    # Snapshot fields
    latest_snapshot_cid: Optional[str] = None
    snapshot_event_cid: Optional[str] = None  # Event CID at snapshot time (checkpoint for mirrors)
    snapshot_seq: int = 0
    snapshot_count: int = 0
    snapshot_ts: Optional[str] = None

    # Totals
    total_count: int = 0  # Total number of unique PIs

    # Metadata
    last_snapshot_trigger: Optional[str] = None  # Timestamp when snapshot was last triggered
    last_updated: str

class Event(BaseModel):
    schema: str = "arke/event@v1"
    type: str  # "create" | "update"
    pi: str
    ver: int
    tip_cid: dict  # IPLD link: {"/": "cid"}
    ts: str  # ISO 8601 timestamp
    prev: Optional[dict] = None  # IPLD link: {"/": "cid"} or null

class Snapshot(BaseModel):
    schema: str = "arke/snapshot@v1"
    seq: int
    ts: str
    prev_snapshot: Optional[dict] = None  # Link to previous snapshot
    event_cid: str  # Event CID at snapshot time (checkpoint for mirrors)
    total_count: int
    entries: list[dict]  # Direct array, no chunking

class AppendEventRequest(BaseModel):
    type: str  # "create" | "update"
    pi: str
    ver: int
    tip_cid: str
