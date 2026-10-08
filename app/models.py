"""Persistence for uploaded files and their extracted features.

Measurement columns are added with the measurement engine in Phase 2.
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class UTCDateTime(TypeDecorator):
    """Stores UTC and always returns timezone-aware UTC (SQLite drops the offset)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=dt.UTC)


class FileStatus:
    PROCESSING = "PROCESSING"
    # Phase-1 interim state: features were read and stored; measurements are NOT computed
    # yet. Phase 2 replaces it with COMPLETED once measuring exists.
    EXTRACTED = "EXTRACTED"
    FAILED = "FAILED"


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(16))
    storage_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)

    crs_status: Mapped[str | None] = mapped_column(String(16))
    crs: Mapped[str | None] = mapped_column(Text)  # EPSG:xxxx or CRS name
    crs_wkt: Mapped[str | None] = mapped_column(Text)
    crs_origin: Mapped[str | None] = mapped_column(String(32))
    feature_count: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    processed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)

    features: Mapped[list["FeatureRecord"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="FeatureRecord.index"
    )


class FeatureRecord(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("file_id", "index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(Text)
    geometry_type: Mapped[str | None] = mapped_column(String(32))
    source_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # source CRS, untouched
    source_crs: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    folder_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    issue_code: Mapped[str | None] = mapped_column(String(64))
    issue_detail: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)

    file: Mapped[FileRecord] = relationship(back_populates="features")
