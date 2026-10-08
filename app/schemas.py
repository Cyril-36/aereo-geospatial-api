import datetime as dt

from pydantic import BaseModel


class Message(BaseModel):
    code: str
    message: str


class FileInfo(BaseModel):
    id: str
    filename: str
    format: str
    size_bytes: int
    sha256: str
    status: str  # PROCESSING | EXTRACTED (Phase-1 interim, not measured) | FAILED
    feature_count: int | None
    crs: str | None
    crs_status: str | None  # KNOWN | UNKNOWN | INVALID
    warnings: list[Message]
    error: Message | None
    created_at: dt.datetime
    processed_at: dt.datetime | None
