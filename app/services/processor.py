"""Ingestion orchestration: stored upload -> Dataset. No HTTP or database code here.

Phase 1 stops after extraction. Measurement is added in Phase 2.
"""

import tempfile
from pathlib import Path

from app.config import Settings
from app.services.dataset import Dataset, FileFormat, IngestionLimits
from app.services.kml_reader import read_kml
from app.services.safe_zip import extract_shapefile
from app.services.shapefile_reader import read_shapefile


def limits_from(settings: Settings) -> IngestionLimits:
    return IngestionLimits(
        max_expanded_bytes=settings.max_expanded_bytes,
        max_archive_members=settings.max_archive_members,
        max_features=settings.max_features,
        max_vertices=settings.max_vertices,
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
