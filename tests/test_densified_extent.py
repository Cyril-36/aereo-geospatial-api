"""Guards that must also hold for the densified geometry, not only for original vertices.

In a projected CRS whose seam is not at 180°, a straight edge between two nearby vertices
can run the long way round the globe; the vertices alone look harmless.
"""

import math

import numpy as np
import pytest
from pyproj import CRS, Transformer

from app.services.measurements import MeasurementLimits, Status
from tests.helpers_measure import feature, line, run, source


def pdc_mercator(*lonlat):
    t = Transformer.from_crs(4326, 3832, always_xy=True)  # PDC Mercator, centred on 150°E
    return [t.transform(*p) for p in lonlat]


def test_line_across_the_projection_seam_is_rejected():
    start, end = (-40, 10), (-20, 10)  # 20° apart; the seam (30°W) lies between them
    projected = pdc_mercator(start, end)
    assert projected[0][0] * projected[1][0] < 0  # x changes sign: the chord spans ~340°
    result = run([feature(line(projected))], source("EPSG:3832"))[0]
    assert (result.status, result.reason_code) == (
        Status.UNSUPPORTED_EXTENT,
        "ANTIMERIDIAN_CROSSING",
    )
    assert result.length_m is None and result.generated_vertices == 0


def test_line_away_from_the_seam_is_measured():
    # A straight EPSG:3832 line along 10°N is the parallel; its length is the parallel arc
    # 2° * N(phi) * cos(phi) on the WGS84 ellipsoid (independent closed form).
    result = run([feature(line(pdc_mercator((150, 10), (152, 10))))], source("EPSG:3832"))[0]
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    phi = math.radians(10)
    arc = math.radians(2) * a / math.sqrt(1 - e2 * math.sin(phi) ** 2) * math.cos(phi)
    assert result.status == Status.MEASURED
    # ~110 km either side of the LAEA centre: length distortion is below 1e-4.
    assert result.length_m == pytest.approx(arc, rel=1e-4)


def test_feature_failing_after_densification_does_not_block_later_features():
    # Feature 0 is densified (~757 inserts) and then rejected by the re-check; its
    # allowance must not be charged, or feature 1 would be refused.
    long_way = line(pdc_mercator((-40, 10), (-20, 10)))
    valid = line(pdc_mercator((150, 10), (152, 10)))  # ~219 km: 4 inserts
    limits = MeasurementLimits(max_total_vertices=2 + 2 + 4)
    failed, measured = run([feature(long_way, 0), feature(valid, 1)], source("EPSG:3832"), limits)
    assert failed.status == Status.UNSUPPORTED_EXTENT and failed.generated_vertices == 0
    assert measured.status == Status.MEASURED and measured.generated_vertices == 4


def test_line_through_a_conic_gap_is_outside_the_projection_domain():
    # Alaska Albers maps the globe onto a fan; its seam is at 26°E. A straight line between
    # 20°E and 35°E crosses the gap between the fan's edges, where PROJ's inverse still
    # returns (wrapped) longitudes: 20 ... 52.8, then -1.1 ... 35.
    t = Transformer.from_crs(4326, 3338, always_xy=True)
    projected = [t.transform(20, 60), t.transform(35, 60)]
    result = run([feature(line(projected))], source("EPSG:3338"))[0]
    assert (result.status, result.reason_code) == (
        Status.TRANSFORM_FAILED,
        "OUTSIDE_PROJECTION_DOMAIN",
    )
    assert result.length_m is None and result.generated_vertices == 0


@pytest.mark.parametrize(
    ("code", "start", "end"),
    [
        ("EPSG:3338", (-160, 60), (-140, 65)),  # Alaska, inside the fan
        ("EPSG:32643", (60, 13), (90, 13)),  # UTM 43N far outside its zone
        ("EPSG:2263", (-74.2, 40.5), (-73.7, 40.9)),  # US survey feet
        ("EPSG:3857", (10, 60), (30, 70)),
        ("EPSG:3413", (0, 75), (170, 75)),  # polar stereographic, near the pole
        ("ESRI:54009", (-100, -20), (-60, 40)),  # Mollweide
    ],
)
def test_domain_guard_accepts_ordinary_projected_lines(code, start, end):
    t = Transformer.from_crs(4326, code, always_xy=True)
    projected = [t.transform(*start), t.transform(*end)]
    result = run([feature(line(projected))], source(code))[0]
    assert result.status == Status.MEASURED, (result.reason_code, result.reason)
    assert result.generated_vertices >= 0


def test_domain_tolerance_has_a_wide_margin():
    # Points inside the domain round-trip to ~1e-7 source units; the tolerance is 1 mm.
    crs = CRS.from_epsg(3413)
    to_geodetic = Transformer.from_crs(crs, crs.geodetic_crs, always_xy=True)
    xy = np.array([[0.0, 0.0], [1e6, -2e6], [-3e6, 3e6]])
    lon, lat = to_geodetic.transform(xy[:, 0], xy[:, 1])
    x, y = to_geodetic.transform(lon, lat, direction="INVERSE")
    assert np.hypot(x - xy[:, 0], y - xy[:, 1]).max() < 1e-5
