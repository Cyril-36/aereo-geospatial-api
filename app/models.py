"""Persistence for uploaded files, their extracted features and measurement results.

Schema changes are applied by ``app.migrations``; see that module for the version history.
"""

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # Extraction, measurement and persistence all succeeded. Individual features may still be
    # skipped or invalid; the per-status counts say so.
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # Legacy: written only by the extraction-only first release. Such files were never
    # measured and are never relabelled COMPLETED; re-upload them to measure.
    EXTRACTED = "EXTRACTED"


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
    # Added in schema version 2.
    source_crs_input: Mapped[str | None] = mapped_column(Text)  # caller's source_crs, if any
    transformation: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # source -> WGS84
    status_counts: Mapped[dict[str, int] | None] = mapped_column(JSON)  # per feature status

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
    # Added in schema version 2: the measurement result. NULL status = never measured.
    status: Mapped[str | None] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    area_m2: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    measurement_method: Mapped[str | None] = mapped_column(String(32))
    measurement_crs: Mapped[str | None] = mapped_column(Text)
    geodesic_area_m2: Mapped[float | None] = mapped_column(Float)
    geodesic_length_m: Mapped[float | None] = mapped_column(Float)
    relative_difference: Mapped[float | None] = mapped_column(Float)
    wgs84_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # only if transformed
    transformation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generated_vertices: Mapped[int | None] = mapped_column(Integer)
    # Added in schema version 3: search and sort keys (see app.services.search).
    display_name: Mapped[str | None] = mapped_column(Text)
    sort_name: Mapped[str | None] = mapped_column(Text)
    search_text: Mapped[str | None] = mapped_column(Text)
    warning_count: Mapped[int | None] = mapped_column(Integer)

    file: Mapped[FileRecord] = relationship(back_populates="features")
