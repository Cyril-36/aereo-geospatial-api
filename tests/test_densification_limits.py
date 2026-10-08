"""Densification counts are bounded before they become integers.

The insert count is ceil(length / step) - 1. A finite but huge value exceeds the int64 range;
casting it wraps to a negative number on x86-64 (bypassing every limit) and saturates on
arm64. Each public-path test first checks that its input really reaches the count.
"""

import math

import numpy as np
import pytest
from pyproj import Transformer

from app.services import measurements as m
from app.services.measurements import MeasurementLimits, Status, VertexBudget
from tests.helpers_measure import feature, line, run, source

INT64_MAX = np.iinfo(np.int64).max


@pytest.fixture
def capacity_calls(monkeypatch):
    calls = []
    real = m._check_capacity

    def spy(inserts, *args):
        calls.append([a.copy() for a in inserts])
        return real(inserts, *args)

    monkeypatch.setattr(m, "_check_capacity", spy)
    return calls


def test_valid_line_with_tiny_step_reaches_the_count_and_is_refused(capacity_calls):
    # A valid 600 km UTM line; a configured 1e-15 m step needs ~6e20 inserts (> int64).
    t = Transformer.from_crs(4326, 32643, always_xy=True)
    coords = [t.transform(72.5, 13), t.transform(78, 13)]
    limits = MeasurementLimits(max_segment_m=1e-15)
    assert math.dist(*coords) / 1e-15 > INT64_MAX
    result = run([feature(line(coords))], source("EPSG:32643"), limits)[0]
    assert len(capacity_calls) == 1
    assert capacity_calls[0][0].dtype == np.float64 and capacity_calls[0][0][0] > INT64_MAX
    assert (result.status, result.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")
    assert result.generated_vertices == 0 and result.length_m is None


def test_infinite_count_is_refused(capacity_calls):
    # A subnormal step makes length / step overflow to infinity.
    limits = MeasurementLimits(max_segment_m=1e-320)
    result = run([feature(line([(77.0, 13.0), (77.1, 13.0)]))], source("EPSG:4326"), limits)[0]
    assert math.isinf(capacity_calls[0][0][0])
    assert (result.status, result.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")


def test_extreme_web_mercator_line(capacity_calls):
    # y = 1e24 converts to latitude 90°, which passes the WGS84 range check, but no latitude
    # projects back to it: it is outside Web Mercator's domain and is refused before
    # densification. (Without the domain guard it reached the count, needing > 2**63 inserts;
    # the count path is covered with valid coordinates by the tiny-step test above.)
    coords = [(0.0, 0.0), (0.0, 1e24)]
    wgs84 = [Transformer.from_crs(3857, 4326, always_xy=True).transform(*c) for c in coords]
    assert wgs84 == [(0.0, 0.0), (0.0, 90.0)]
    result = run([feature(line(coords))], source("EPSG:3857"))[0]
    assert capacity_calls == []
    assert (result.status, result.reason_code) == (
        Status.TRANSFORM_FAILED,
        "OUTSIDE_PROJECTION_DOMAIN",
    )
    assert result.generated_vertices == 0


def test_non_finite_segment_length():
    coords = [(0.0, -1e308), (0.0, 1e308)]
    wgs84 = [Transformer.from_crs(3857, 4326, always_xy=True).transform(*c) for c in coords]
    assert wgs84 == [(0.0, -90.0), (0.0, 90.0)]
    assert math.isinf(math.hypot(0.0, 1e308 - -1e308))
    result = run([feature(line(coords))], source("EPSG:3857"))[0]
    # Also outside the projection's domain, which is now checked first; the non-finite-length
    # guard itself is exercised directly below.
    assert (result.status, result.reason_code) == (
        Status.TRANSFORM_FAILED,
        "OUTSIDE_PROJECTION_DOMAIN",
    )


def test_plan_rejects_non_finite_lengths_directly():
    geometry = m.shape(line([(0.0, -1e308), (0.0, 1e308)]))
    with pytest.raises(m._Stop) as exc_info:
        m._plan_inserts(geometry, m._planar_lengths, 50_000.0)
    assert (exc_info.value.status, exc_info.value.code) == (
        Status.UNSUPPORTED_EXTENT,
        "NON_FINITE_LENGTH",
    )


def test_horizontal_extreme_line_is_never_measured():
    # PROJ's inverse Mercator for x = 1e24 m is platform-dependent: longitude -757.9° on
    # arm64 (stopped by the range check) but -180.0° on x86-64, which passes the range check
    # and reaches densification. Either way the feature must be refused, never measured.
    result = run([feature(line([(0, 0), (1e24, 0)]))], source("EPSG:3857"))[0]
    assert result.status != Status.MEASURED
    assert result.length_m is None and result.generated_vertices == 0
    # With the projection-domain guard both platforms stop it as a failed conversion: the
    # range check on arm64, the round trip (no longitude projects to x = 1e24) on x86-64.
    assert result.status == Status.TRANSFORM_FAILED
    assert result.reason_code in {"COORDINATES_OUT_OF_RANGE", "OUTSIDE_PROJECTION_DOMAIN"}


@pytest.mark.parametrize(
    "count", [2.0**63, float(np.nextafter(2.0**63, 0)), 2.0**64, 1e300, math.inf]
)
def test_capacity_refuses_counts_near_and_beyond_int64(count):
    budget = VertexBudget(10**6)
    inserts = [np.array([count])]
    with pytest.raises(m._Stop) as exc_info:
        m._check_capacity(inserts, 2, MeasurementLimits(), budget)
    assert exc_info.value.code == "DENSIFICATION_LIMIT"
    assert inserts[0].dtype == np.float64  # never converted
    assert budget.remaining == 10**6


def test_nan_count_is_refused_not_passed_through():
    # NaN compares False against every limit, so only the explicit finiteness check stops it.
    budget = VertexBudget(10**6)
    with pytest.raises(m._Stop) as exc_info:
        m._check_capacity([np.array([math.nan])], 2, MeasurementLimits(), budget)
    assert exc_info.value.code == "DENSIFICATION_LIMIT"
    assert budget.remaining == 10**6


@pytest.mark.parametrize("step", [math.nan, 0.0])
def test_degenerate_configured_step_is_refused(step):
    # A zero-length segment divided by a zero (or NaN) step gives NaN inserts.
    geometry = line([(77.0, 13.0), (77.0, 13.0), (77.1, 13.0)])
    result = run([feature(geometry)], source("EPSG:4326"), MeasurementLimits(max_segment_m=step))[0]
    assert (result.status, result.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")
    assert result.generated_vertices == 0


def test_capacity_per_feature_boundary():
    limits = MeasurementLimits(max_vertices_per_feature=10)
    allowed = [np.array([2.0, 4.0])]  # 4 original + 6 inserted = 10
    m._check_capacity(allowed, 4, limits, VertexBudget(100))
    assert allowed[0].dtype == np.int64 and allowed[0].tolist() == [2, 4]
    with pytest.raises(m._Stop):
        m._check_capacity([np.array([3.0, 4.0])], 4, limits, VertexBudget(100))  # 11


def test_capacity_file_budget_boundary():
    m._check_capacity([np.array([5.0])], 2, MeasurementLimits(), VertexBudget(5))
    with pytest.raises(m._Stop) as exc_info:
        m._check_capacity([np.array([6.0])], 2, MeasurementLimits(), VertexBudget(5))
    assert exc_info.value.code == "DENSIFICATION_LIMIT"


def test_plan_counts_are_never_negative():
    geometry = m.shape(line([(0, 0), (0, 0), (10, 0)]))  # zero-length, then 10 units
    plans = m._plan_inserts(geometry, m._planar_lengths, 3.0)
    assert plans[0].tolist() == [0.0, 3.0]


def test_ordinary_datasets_are_unchanged():
    # 110.6 km geographic line -> 2 inserts; 290 km in US feet -> 5 inserts.
    assert run([feature(line([(0, 0), (0, 1)]))], source("EPSG:4326"))[0].generated_vertices == 2
    feet = 290_000 / (1200 / 3937)
    result = run([feature(line([(1e6, 2e5), (1e6 + feet, 2e5)]))], source("EPSG:2263"))[0]
    assert result.generated_vertices == 5
