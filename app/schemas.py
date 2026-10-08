import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class Message(BaseModel):
    code: str
    message: str


class Transformation(BaseModel):
    operation: str
    accuracy_m: float | None = Field(description="Stated accuracy; null when PROJ states none")
    best_available: bool


class FileInfo(BaseModel):
    id: str
    filename: str
    format: str
    size_bytes: int
    sha256: str
    status: str = Field(
        description="PROCESSING | COMPLETED | FAILED; EXTRACTED marks legacy, unmeasured files"
    )
    feature_count: int | None
    counts: dict[str, int] | None = Field(description="Features per measurement status")
    crs: str | None = Field(description="Source CRS: EPSG code or name; null when unknown")
    crs_status: str | None = Field(description="KNOWN | UNKNOWN | INVALID")
    crs_origin: str | None = Field(description="PRJ_FILE | KML_SPECIFICATION | OVERRIDE | NONE")
    transformation: Transformation | None
    warnings: list[Message]
    error: Message | None
    created_at: dt.datetime
    processed_at: dt.datetime | None


class GeodesicReference(BaseModel):
    area_m2: float | None = None
    length_m: float | None = None
    relative_difference: float | None


class FeatureMeasurement(BaseModel):
    index: int
    source_id: str | None
    geometry_type: str | None
    geometry: dict[str, Any] | None
    geometry_crs: str | None = Field(description="CRS of `geometry`; null when unknown")
    geometry_origin: str | None = Field(description="TRANSFORMED (to WGS84) | SOURCE")
    source_crs: str | None
    properties: dict[str, Any]
    folder_path: list[str]
    status: str | None
    reason_code: str | None
    reason: str | None
    area_m2: float | None
    length_m: float | None
    measurement_method: str | None
    measurement_crs: str | None
    geodesic_reference: GeodesicReference | None
    transformation: Transformation | None
    generated_vertices: int | None
    warnings: list[Message]


class Pagination(BaseModel):
    limit: int
    offset: int
    total: int
    returned: int
    next_offset: int | None


class MeasurementsPage(BaseModel):
    file_id: str
    status: str
    crs: str | None
    feature_count: int
    counts: dict[str, int]
    pagination: Pagination
    features: list[FeatureMeasurement]
