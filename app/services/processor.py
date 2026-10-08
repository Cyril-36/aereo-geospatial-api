"""Processing orchestration: stored upload -> extracted features -> measurements.

No HTTP or database code here. Ingestion and file-level CRS errors are raised as
IngestionError; every per-feature problem is a status on that feature.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.services.dataset import Dataset, FileFormat, IngestionLimits
from app.services.kml_reader import read_kml
from app.services.measurements import MeasuredDataset, MeasurementLimits, measure_dataset
from app.services.safe_zip import extract_shapefile
from app.services.shapefile_reader import read_shapefile


def limits_from(settings: Settings) -> IngestionLimits:
    return IngestionLimits(
        max_expanded_bytes=settings.max_expanded_bytes,
        max_archive_members=settings.max_archive_members,
        max_features=settings.max_features,
        max_vertices=settings.max_vertices,
    )


def measurement_limits_from(settings: Settings) -> MeasurementLimits:
    return MeasurementLimits(
        max_segment_m=settings.densify_max_segment_m,
        max_vertices_per_feature=settings.max_vertices_per_feature,
        max_total_vertices=settings.max_vertices,
        relative_tolerance=settings.geodesic_relative_tolerance,
    )


def extract(
    upload: Path, file_format: FileFormat, limits: IngestionLimits, work_dir: Path
) -> Dataset:
    if file_format == "KML":
        return read_kml(upload, limits)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Extracted components live only for the duration of the read, on success or failure.
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        components = extract_shapefile(upload, Path(tmp), limits)
        return read_shapefile(components, limits)


@dataclass(frozen=True)
class ProcessedFile:
    dataset: Dataset
    measured: MeasuredDataset


def process(
    upload: Path, file_format: FileFormat, settings: Settings, source_crs: str | None = None
) -> ProcessedFile:
    dataset = extract(upload, file_format, limits_from(settings), settings.work_dir)
    measured = measure_dataset(dataset, measurement_limits_from(settings), source_crs)
    return ProcessedFile(dataset, measured)
