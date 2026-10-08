"""Domain objects produced by the readers. Independent of HTTP and the database."""

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from app.errors import IngestionError

CrsStatus = Literal["KNOWN", "UNKNOWN", "INVALID"]
FileFormat = Literal["SHAPEFILE", "KML"]


@dataclass(frozen=True)
class SourceCrs:
    """CRS declared by the source file.

    ``UNKNOWN`` means nothing was declared (e.g. a Shapefile without ``.prj``) and is never
    replaced by a guess. ``INVALID`` means something was declared but could not be parsed.
    """

    status: CrsStatus
    identifier: str | None  # "EPSG:4326", or the CRS name when no EPSG code matches
    wkt: str | None
    origin: Literal["KML_SPECIFICATION", "PRJ_FILE", "OVERRIDE", "NONE"]


@dataclass(frozen=True)
class IngestionLimits:
    max_expanded_bytes: int
    max_archive_members: int
    max_features: int
    max_vertices: int


@dataclass
class RawFeature:
    """One source feature exactly as read: original coordinates, no repair, no reprojection."""

    index: int  # 0-based position in the source, stable ordering key
    source_id: str | None  # Shapefile FID or KML Placemark id attribute
    geometry_type: str | None
    geometry: dict[str, Any] | None  # GeoJSON-style mapping in the *source* CRS
    properties: dict[str, Any]
    folder_path: list[str] = field(default_factory=list)  # KML Document/Folder names
    # A reader-level problem with this one feature; other features are unaffected.
    issue_code: str | None = None
    issue_detail: str | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Dataset:
    format: FileFormat
    source_crs: SourceCrs
    features: list[RawFeature]
    warnings: list[dict[str, str]] = field(default_factory=list)


def warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


class FeatureBudget:
    """Enforces feature and vertex limits. Exceeding one fails the file; nothing is truncated."""

    def __init__(self, limits: IngestionLimits) -> None:
        self.limits = limits
        self.features = 0
        self.vertices = 0

    def add_feature(self) -> None:
        self.features += 1
        if self.features > self.limits.max_features:
            raise IngestionError(
                "TOO_MANY_FEATURES",
                f"File has more than the {self.limits.max_features}-feature limit.",
            )

    def add_vertices(self, count: int) -> None:
        self.vertices += count
        if self.vertices > self.limits.max_vertices:
            raise IngestionError(
                "TOO_MANY_VERTICES",
                f"File has more than the {self.limits.max_vertices}-vertex limit.",
            )


def iter_positions(geometry: dict[str, Any]):
    """Yield every coordinate position of a GeoJSON-style geometry."""
    if geometry.get("type") == "GeometryCollection":
        for part in geometry.get("geometries") or []:
            if isinstance(part, dict):
                yield from iter_positions(part)
        return
    stack = [geometry.get("coordinates")]
    while stack:
        item = stack.pop()
        if not isinstance(item, (list, tuple)):
            continue  # malformed input is reported by the measurement stage, not here
        if item and isinstance(item[0], (int, float)):
            yield item
        else:
            stack.extend(item)


def json_safe(value: Any) -> Any:
    """Make an attribute JSON-safe: non-finite floats become None, dates ISO text."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value
