"""Fail-closed validation at the persistence boundary.

A MEASURED result must carry exactly its own finite, non-negative metric and geodesic
reference; any other status must carry none. Nothing inconsistent is stored, and nothing is
"fixed" by turning an invalid number into NULL while keeping MEASURED.
"""

import math

import pytest

from app.models import FileRecord
from app.results import InconsistentResultError, apply_results, validate_measurement
from app.services.crs import kml_crs, resolve
from app.services.dataset import Dataset, RawFeature
from app.services.measurements import FeatureMeasurement, MeasuredDataset
from app.services.processor import ProcessedFile


def processed(kind: str, *measurements: FeatureMeasurement) -> ProcessedFile:
    features = [RawFeature(m.index, None, kind, None, {}) for m in measurements]
    return ProcessedFile(
        Dataset("KML", kml_crs(), features),
        MeasuredDataset(resolve(kml_crs()), list(measurements), []),
    )


def measured_line(**overrides) -> FeatureMeasurement:
    values = {"length_m": 10.0, "geodesic_length_m": 10.0, "relative_difference": 0.0}
    values.update(overrides)
    return FeatureMeasurement(0, "MEASURED", **values)


def test_valid_zero_is_stored_as_zero():
    record = FileRecord(id="x", warnings=[])
    zero = measured_line(length_m=0.0, geodesic_length_m=0.0, relative_difference=None)
    apply_results(record, processed("LineString", zero))
    stored = record.features[0]
    assert (stored.status, stored.length_m, stored.geodesic_length_m) == ("MEASURED", 0.0, 0.0)
    assert record.status_counts == {"MEASURED": 1}


@pytest.mark.parametrize(
    "overrides",
    [
        {"length_m": math.nan},
        {"length_m": math.inf},
        {"length_m": None},  # MEASURED with a null metric: previously stored as such
        {"length_m": -1.0},
        {"geodesic_length_m": math.nan},
        {"geodesic_length_m": None},
        {"geodesic_length_m": -0.5},
        {"relative_difference": math.inf},
        {"area_m2": 5.0},  # a line carrying an area
        {"generated_vertices": -1},
    ],
)
def test_inconsistent_measured_line_is_refused(overrides):
    record = FileRecord(id="x", warnings=[])
    with pytest.raises(InconsistentResultError):
        apply_results(record, processed("LineString", measured_line(**overrides)))
    assert record.features == [] and record.status_counts is None  # nothing was assigned


@pytest.mark.parametrize(
    "overrides",
    [
        {"area_m2": math.nan, "geodesic_area_m2": 1.0},
        {"area_m2": None, "geodesic_area_m2": 1.0},
        {"area_m2": 1.0, "geodesic_area_m2": math.inf},
        {"area_m2": 1.0, "geodesic_area_m2": 1.0, "length_m": 2.0},
    ],
)
def test_inconsistent_measured_polygon_is_refused(overrides):
    with pytest.raises(InconsistentResultError):
        validate_measurement("Polygon", FeatureMeasurement(0, "MEASURED", **overrides))


def test_measured_point_or_collection_is_refused():
    with pytest.raises(InconsistentResultError):
        validate_measurement("Point", FeatureMeasurement(0, "MEASURED"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"length_m": 3.0},
        {"geodesic_area_m2": 0.0},
        {"relative_difference": 0.1},
        {"generated_vertices": 4},
    ],
)
def test_unmeasured_feature_carrying_numbers_is_refused(overrides):
    with pytest.raises(InconsistentResultError):
        validate_measurement("LineString", FeatureMeasurement(0, "TRANSFORM_FAILED", **overrides))


def test_unmeasured_features_without_numbers_are_accepted():
    for status in ("NOT_APPLICABLE", "UNKNOWN_CRS", "INVALID_GEOMETRY", "TRANSFORM_FAILED"):
        validate_measurement("Polygon", FeatureMeasurement(0, status))


def test_one_bad_result_refuses_the_whole_set_before_assignment():
    record = FileRecord(id="x", warnings=[])
    good = measured_line()
    bad = FeatureMeasurement(1, "MEASURED", length_m=math.nan, geodesic_length_m=1.0)
    with pytest.raises(InconsistentResultError):
        apply_results(record, processed("LineString", good, bad))
    assert record.features == [] and record.feature_count is None
