"""The file-wide vertex budget is checked before densifying but charged only on success."""

import math

import numpy as np
import pytest

from app.services import measurements as m
from app.services.crs import kml_crs
from app.services.measurements import MeasurementLimits, Status, VertexBudget
from tests.helpers_measure import feature, line, run


def test_commit_rejects_impossible_counts():
    budget = VertexBudget(5)
    with pytest.raises(RuntimeError):
        budget.commit(-1)
    with pytest.raises(RuntimeError):
        budget.commit(6)
    budget.commit(5)
    assert budget.remaining == 0


def test_check_does_not_charge():
    budget = VertexBudget(5)
    budget.check(5)
    assert budget.remaining == 5


def test_laea_failure_after_reservation_releases_the_allowance(monkeypatch):
    real_apply = m._apply
    failed = []

    def fail_first_laea(geometry, transformer):
        out = real_apply(geometry, transformer)
        if "proj=laea" in transformer.definition and not failed:
            failed.append(True)
            return m.shapely.transform(out, lambda xy: np.full_like(xy, np.inf))
        return out

    monkeypatch.setattr(m, "_apply", fail_first_laea)
    # Each 10° meridian line needs 22 inserts; the file budget has room for exactly one.
    first, second = run(
        [feature(line([(0, 0), (0, 10)]), 0), feature(line([(1, 0), (1, 10)]), 1)],
        kml_crs(),
        MeasurementLimits(max_total_vertices=4 + 22),
    )
    assert (first.status, first.reason_code) == (Status.TRANSFORM_FAILED, "LAEA_PROJECTION_FAILED")
    assert first.generated_vertices == 0
    assert second.status == Status.MEASURED and second.generated_vertices == 22


def test_budget_is_still_enforced_across_successful_features():
    first, second = run(
        [feature(line([(0, 0), (0, 10)]), 0), feature(line([(1, 0), (1, 10)]), 1)],
        kml_crs(),
        MeasurementLimits(max_total_vertices=4 + 22),
    )
    assert first.status == Status.MEASURED and first.generated_vertices == 22
    assert (second.status, second.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")


def test_generated_vertices_are_points_actually_inserted():
    results = run(
        [
            feature(line([(0, 0), (0, 1)]), 0),  # 110.6 km -> 2
            feature(line([(10, 0), (10, 0.3)]), 1),  # 33 km -> 0
            feature({"type": "Point", "coordinates": [0, 0]}, 2),
            feature(line([(179, 0), (-179, 0)]), 3),  # antimeridian: not measured
            feature(line([("a", "b")]), 4),  # invalid
        ],
        kml_crs(),
    )
    assert [r.generated_vertices for r in results] == [2, 0, 0, 0, 0]


def test_failure_in_final_calculation_charges_nothing(monkeypatch):
    def broken_reference(_geometry):
        raise RuntimeError("geodesic reference failed")

    monkeypatch.setattr(m, "_geodesic_length", broken_reference)
    budget = VertexBudget(100)
    result = m.FeatureMeasurement(index=0)
    with pytest.raises(RuntimeError):
        m._measure(
            feature(line([(0, 0), (0, 1)])),  # 2 inserts
            m.resolve(kml_crs()),
            MeasurementLimits(),
            budget,
            result,
        )
    assert budget.remaining == 100
    assert result.status == "" and result.generated_vertices == 0


# --- Numerical validation of calculated values ------------------------------------------


def laea_scaled(monkeypatch, factor, times=None):
    """Scale coordinates after projection to the local LAEA (for the first ``times`` features,
    or all): finite coordinates whose area or length overflow, as an injected numerical
    failure."""
    real_apply = m._apply
    applied = []

    def scaled(geometry, transformer):
        out = real_apply(geometry, transformer)
        if "proj=laea" in transformer.definition and (times is None or len(applied) < times):
            applied.append(1)
            return m.shapely.transform(out, lambda xy: xy * factor)
        return out

    monkeypatch.setattr(m, "_apply", scaled)


POLYGON = {
    "type": "Polygon",
    "coordinates": [[[77, 13], [77.01, 13], [77.01, 13.01], [77, 13.01], [77, 13]]],
}


def test_overflowing_area_is_transform_failed_and_later_features_still_measure(monkeypatch):
    laea_scaled(monkeypatch, 1e200, times=1)  # finite coordinates (~1e203); the area overflows
    first, second = run(
        [feature(POLYGON, 0), feature(line([(1, 0), (1, 10)]), 1)],
        kml_crs(),
        MeasurementLimits(max_total_vertices=5 + 2 + 22),  # room for the line's 22 inserts
    )
    assert (first.status, first.reason_code) == (Status.TRANSFORM_FAILED, "NON_FINITE_MEASUREMENT")
    assert (first.area_m2, first.geodesic_area_m2, first.generated_vertices) == (None, None, 0)
    assert second.status == Status.MEASURED and second.generated_vertices == 22


@pytest.mark.parametrize(
    ("target", "value", "code"),
    [
        ("_geodesic_area", math.nan, "NON_FINITE_MEASUREMENT"),
        ("_geodesic_area", math.inf, "NON_FINITE_MEASUREMENT"),
        ("_geodesic_area", -1.0, "NEGATIVE_MEASUREMENT"),
    ],
)
def test_invalid_reference_is_refused_without_charging_the_budget(monkeypatch, target, value, code):
    monkeypatch.setattr(m, target, lambda _g: value)
    budget = VertexBudget(100)
    result = m.measure_feature(feature(POLYGON), m.resolve(kml_crs()), MeasurementLimits(), budget)
    assert (result.status, result.reason_code) == (Status.TRANSFORM_FAILED, code)
    assert (result.area_m2, result.geodesic_area_m2, result.relative_difference) == (
        None,
        None,
        None,
    )
    assert budget.remaining == 100


def test_failed_numerical_feature_does_not_block_a_later_valid_feature(monkeypatch):
    real = m._geodesic_length
    calls = []

    def nan_once(geometry):
        calls.append(1)
        return math.nan if len(calls) == 1 else real(geometry)

    monkeypatch.setattr(m, "_geodesic_length", nan_once)
    first, second = run(
        [feature(line([(0, 0), (0, 10)]), 0), feature(line([(1, 0), (1, 10)]), 1)],
        kml_crs(),
        MeasurementLimits(max_total_vertices=4 + 22),  # room for exactly one line's inserts
    )
    assert (first.status, first.reason_code) == (Status.TRANSFORM_FAILED, "NON_FINITE_MEASUREMENT")
    assert second.status == Status.MEASURED and second.generated_vertices == 22


def test_zero_reference_with_non_zero_value_warns_without_a_percentage(monkeypatch):
    monkeypatch.setattr(m, "_geodesic_length", lambda _g: 0.0)
    result = run([feature(line([(77, 13), (77.01, 13)]))], kml_crs())[0]
    assert result.status == Status.MEASURED
    assert result.geodesic_length_m == 0.0 and result.relative_difference is None
    (disagreement,) = [w for w in result.warnings if w["code"] == "GEODESIC_DISAGREEMENT"]
    assert "reference is zero" in disagreement["message"] and "%" not in disagreement["message"]


def test_valid_zero_is_measured_as_zero(monkeypatch):
    laea_scaled(monkeypatch, 0.0)  # collapse the projected geometry to zero length
    monkeypatch.setattr(m, "_geodesic_length", lambda _g: 0.0)
    result = run([feature(line([(77, 13), (77.01, 13)]))], kml_crs())[0]
    assert (result.status, result.length_m, result.geodesic_length_m) == (Status.MEASURED, 0.0, 0.0)
    assert result.relative_difference is None
    assert not [w for w in result.warnings if w["code"] == "GEODESIC_DISAGREEMENT"]
