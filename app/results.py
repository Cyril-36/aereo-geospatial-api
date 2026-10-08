"""Mapping between processing results, database records and API responses.

Rules applied here:
- every extracted feature is stored, including skipped and invalid ones;
- reader warnings and measurement warnings are both kept, reader warnings first;
- a measurement is NULL when it was not calculated; 0 is a calculated zero;
- results are validated before anything is stored (fail closed): a MEASURED feature has
  exactly its own finite, non-negative metric and geodesic reference, other statuses have
  none. An inconsistent result raises InconsistentResultError; it is never "fixed" by turning
  an invalid number into NULL while keeping MEASURED;
- geometry is labelled EPSG:4326 only when it is the successfully transformed copy, or when
  the source itself is EPSG:4326; otherwise it is the original coordinates, labelled with the
  source CRS, or with null when that CRS is unknown.
"""

import math
from collections import Counter
from typing import Any

from app.models import FeatureRecord, FileRecord
from app.services.crs import WGS84
from app.services.measurements import AREA_TYPES, LENGTH_TYPES, FeatureMeasurement, Status
from app.services.processor import ProcessedFile


class InconsistentResultError(RuntimeError):
    """A measurement result contradicts its own status; nothing may be stored."""


def _valid_metric(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def validate_measurement(geometry_type: str | None, result: FeatureMeasurement) -> None:
    """Raise InconsistentResultError unless the result's numbers agree with its status."""
    area = (result.area_m2, result.geodesic_area_m2)
    length = (result.length_m, result.geodesic_length_m)
    where = f"feature {result.index} ({result.status})"
    if result.status == Status.MEASURED:
        if geometry_type in AREA_TYPES:
            expected, unexpected = area, length
        elif geometry_type in LENGTH_TYPES:
            expected, unexpected = length, area
        else:
            raise InconsistentResultError(f"{where}: {geometry_type} cannot be measured")
        if not all(_valid_metric(v) for v in expected):
            raise InconsistentResultError(f"{where}: metric or reference is not a finite >= 0")
        if any(v is not None for v in unexpected):
            raise InconsistentResultError(f"{where}: carries the other geometry kind's metric")
        if result.relative_difference is not None and not _valid_metric(result.relative_difference):
            raise InconsistentResultError(f"{where}: invalid relative difference")
        if not isinstance(result.generated_vertices, int) or result.generated_vertices < 0:
            raise InconsistentResultError(f"{where}: invalid generated vertex count")
        return
    numbers = (*area, *length, result.relative_difference)
    if any(v is not None for v in numbers) or result.generated_vertices != 0:
        raise InconsistentResultError(f"{where}: an unmeasured feature carries measurements")


def transformation_dict(processed: ProcessedFile) -> dict[str, Any] | None:
    t = processed.measured.crs.transformation
    if t is None:
        return None
    return {
        "operation": t.operation,
        "accuracy_m": t.accuracy_m,
        "best_available": t.best_available,
    }


def apply_results(record: FileRecord, processed: ProcessedFile) -> None:
    """Fill a file record and its feature records from a processed upload (no commit)."""
    dataset, measured = processed.dataset, processed.measured
    for raw, result in zip(dataset.features, measured.features, strict=True):
        validate_measurement(raw.geometry_type, result)  # before anything is assigned
    source = measured.crs.source  # the declared CRS, or the caller's override
    transformation = transformation_dict(processed)

    record.crs_status = source.status
    record.crs = source.identifier
    record.crs_wkt = source.wkt
    record.crs_origin = source.origin
    record.transformation = transformation
    record.feature_count = len(dataset.features)
    record.warnings = [*dataset.warnings, *measured.warnings]
    record.status_counts = dict(sorted(Counter(m.status for m in measured.features).items()))
    record.features = [
        FeatureRecord(
            index=raw.index,
            source_id=raw.source_id,
            geometry_type=raw.geometry_type,
            source_geometry=raw.geometry,
            source_crs=source.identifier,
            properties=raw.properties,
            folder_path=raw.folder_path,
            issue_code=raw.issue_code,
            issue_detail=raw.issue_detail,
            warnings=[*raw.warnings, *result.warnings],
            status=result.status,
            reason_code=result.reason_code,
            reason=result.reason,
            area_m2=result.area_m2,
            length_m=result.length_m,
            measurement_method=result.measurement_method,
            measurement_crs=result.measurement_crs,
            geodesic_area_m2=result.geodesic_area_m2,
            geodesic_length_m=result.geodesic_length_m,
            relative_difference=result.relative_difference,
            wgs84_geometry=result.wgs84_geometry,
            transformation=transformation if result.wgs84_geometry is not None else None,
            generated_vertices=result.generated_vertices,
        )
        for raw, result in zip(dataset.features, measured.features, strict=True)
    ]


def geometry_out(feature: FeatureRecord) -> tuple[dict | None, str | None, str | None]:
    """(geometry, geometry_crs, geometry_origin) with truthful CRS labelling."""
    if feature.wgs84_geometry is not None:
        return feature.wgs84_geometry, WGS84, "TRANSFORMED"
    if feature.source_geometry is not None:
        # Original coordinates, in the source CRS (null when unknown); never relabelled WGS84.
        return feature.source_geometry, feature.source_crs, "SOURCE"
    return None, None, None


def geodesic_reference(feature: FeatureRecord) -> dict[str, float | None] | None:
    if feature.geodesic_area_m2 is not None:
        return {
            "area_m2": feature.geodesic_area_m2,
            "relative_difference": feature.relative_difference,
        }
    if feature.geodesic_length_m is not None:
        return {
            "length_m": feature.geodesic_length_m,
            "relative_difference": feature.relative_difference,
        }
    return None
