"""Measurement engine tests.

Expected values never come from the code under test. They come from:
- published constants (the WGS84 meridian arc);
- PROJ's analytic projection scale factors (``Proj.get_factors``);
- Karney's geodesic algorithm (``pyproj.Geod``), called directly by the test on the
  test's own vertices. It is an independent algorithm from the LAEA path measured here.
Tolerances are derived from the expected projection distortion, as noted per test.
"""

import math

import pytest
from pyproj import CRS, Geod, Proj, Transformer

from app.errors import IngestionError
from app.services import crs
from app.services.dataset import Dataset, RawFeature, SourceCrs
from app.services.measurements import MeasurementLimits, Status, measure_dataset

GEOD = Geod(ellps="WGS84")
US_FOOT = 1200 / 3937  # metres


def source(code: str) -> SourceCrs:
    return SourceCrs("KNOWN", code, CRS.from_user_input(code).to_wkt(), "PRJ_FILE")


def feature(geometry: dict | None, index: int = 0, **kwargs) -> RawFeature:
    kind = geometry["type"] if geometry else None
    return RawFeature(index, None, kind, geometry, {}, **kwargs)


def polygon(*rings) -> dict:
    return {"type": "Polygon", "coordinates": [list(map(list, r)) for r in rings]}


def line(coords) -> dict:
    return {"type": "LineString", "coordinates": list(map(list, coords))}


def measure(geometry, crs_code="EPSG:4326", limits=None, override=None, **kwargs):
    src = source(crs_code) if crs_code else SourceCrs("UNKNOWN", None, None, "NONE")
    result = measure_dataset(
        Dataset("SHAPEFILE", src, [feature(geometry, **kwargs)]), limits, override
    )
    return result.features[0]


def box(lon0, lat0, lon1, lat1, reverse=False):
    ring = [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]
    return ring[::-1] if reverse else ring


def geod_area(ring) -> float:
    lons, lats = zip(*ring, strict=True)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def geodesic_square(lon, lat, side_m):
    """Square built from geodesic sides of ``side_m`` heading east and north."""
    e = GEOD.fwd(lon, lat, 90, side_m)
    n = GEOD.fwd(lon, lat, 0, side_m)
    ne = GEOD.fwd(e[0], e[1], 0, side_m)
    return [(lon, lat), (e[0], e[1]), (ne[0], ne[1]), (n[0], n[1]), (lon, lat)]


def codes(result) -> list[str]:
    return [w["code"] for w in result.warnings]


# --- correct values against independent references ---------------------------------


def test_geographic_1km_square_matches_geodesic_area():
    ring = geodesic_square(77.0, 13.0, 1000.0)
    result = measure(polygon(ring))
    assert result.status == Status.MEASURED
    assert result.measurement_method == "LOCAL_LAEA"
    assert result.measurement_crs.startswith("+proj=laea +lat_0=13.00")
    # LAEA is equal-area; for a 1 km feature the straight-vs-geodesic edge difference is ~1e-10.
    assert result.area_m2 == pytest.approx(geod_area(ring), rel=1e-8)
    assert result.area_m2 == pytest.approx(1_000_000, rel=1e-4)
    assert codes(result) == []


@pytest.mark.parametrize(
    ("x0", "approx_ground"),
    [(500_000, 1_000_800.5), (825_000, 998_184)],  # central meridian 75°E, and near 78°E
)
def test_utm_43n_grid_square_reports_ground_area_not_grid_area(x0, approx_ground):
    y0 = 1_434_000
    ring = [(x0, y0), (x0 + 1000, y0), (x0 + 1000, y0 + 1000), (x0, y0 + 1000), (x0, y0)]
    result = measure(polygon(ring), "EPSG:32643")
    # Independent reference: PROJ's analytic areal scale of UTM 43N at the square's centre.
    lon, lat = Transformer.from_crs(32643, 4326, always_xy=True).transform(x0 + 500, y0 + 500)
    areal_scale = Proj("EPSG:32643").get_factors(lon, lat).areal_scale
    # Scale varies by <1e-5 across 1 km, so a midpoint value is good to ~1e-10.
    assert result.area_m2 == pytest.approx(1_000_000 / areal_scale, rel=1e-6)
    assert result.area_m2 == pytest.approx(approx_ground, abs=5)
    assert result.area_m2 != pytest.approx(1_000_000, rel=1e-4)  # grid area is not reported


def test_same_polygon_in_degrees_metres_and_us_feet_agrees():
    ring = box(-73.990, 40.755, -73.978, 40.764)  # Midtown Manhattan, ~1 km
    reference = geod_area(ring)
    areas = {}
    for code in ("EPSG:4326", "EPSG:32618", "EPSG:2263"):
        t = Transformer.from_crs(4326, code, always_xy=True)
        projected = [t.transform(x, y) for x, y in ring]
        areas[code] = measure(polygon(projected), code).area_m2
    for code, area in areas.items():
        # Edges are straight in different CRSs; at 1 km the difference is ~1e-9 relative.
        assert area == pytest.approx(reference, rel=1e-6), code
    assert areas["EPSG:2263"] == pytest.approx(areas["EPSG:32618"], rel=1e-7)


def test_us_feet_densification_converts_50km_to_feet():
    # A 290 km straight line in EPSG:2263 (US survey feet). 50 km per segment needs
    # ceil(290/50) - 1 = 5 inserted points. Treating 50,000 as feet would insert 19.
    x0, y0 = 1_000_000.0, 200_000.0
    length_ft = 290_000 / US_FOOT
    result = measure(line([(x0, y0), (x0 + length_ft, y0)]), "EPSG:2263")
    assert result.status == Status.MEASURED
    assert result.generated_vertices == 5
    assert "GEODESIC_DISAGREEMENT" not in codes(result)


def test_web_mercator_at_60n_is_not_measured_in_its_grid():
    t = Transformer.from_crs(4326, 3857, always_xy=True)
    x0, y0 = t.transform(10.0, 60.0)
    ring = [(x0, y0), (x0 + 1000, y0), (x0 + 1000, y0 + 1000), (x0, y0 + 1000), (x0, y0)]
    inverse = Transformer.from_crs(3857, 4326, always_xy=True)
    reference = geod_area([inverse.transform(x, y) for x, y in ring])
    result = measure(polygon(ring), "EPSG:3857")
    assert result.area_m2 == pytest.approx(reference, rel=1e-6)
    # Mercator scale at 60° is ~1/cos(60°) = 2 per axis, so ground area is ~1/4 of grid area.
    assert 0.24 < result.area_m2 / 1_000_000 < 0.26


def test_one_degree_meridian_at_equator():
    result = measure(line([(0, 0), (0, 1)]))
    # Published WGS84 meridian arc from 0° to 1° latitude: 110,574.389 m.
    assert result.geodesic_length_m == pytest.approx(110_574.389, abs=0.01)
    # LAEA's radial scale is cos(c/2); along a line through the centre the mean length error
    # is about -c_max²/24 with c_max = 0.5° of arc: -3.17e-6.
    expected_error = -(math.radians(0.5) ** 2) / 24
    assert (result.length_m - 110_574.389) / 110_574.389 == pytest.approx(expected_error, rel=0.05)
    assert result.generated_vertices == 2  # 110.6 km in segments of at most 50 km


def test_ten_degree_polygon_needs_densification():
    ring = box(70, 5, 80, 15)
    reference = geod_area(ring)  # geodesic edges between the four corners

    densified = measure(polygon(ring))
    # Residual chord error with 50 km segments is ~1e-5 of area or less.
    assert densified.area_m2 == pytest.approx(reference, rel=1e-5)
    assert codes(densified) == []

    no_densify = measure(polygon(ring), limits=MeasurementLimits(max_segment_m=1e12))
    error = (no_densify.area_m2 - reference) / reference
    assert -0.0040 < error < -0.0034  # straight LAEA chords vs geodesic edges: about -0.37 %
    assert "GEODESIC_DISAGREEMENT" in codes(no_densify)


def test_polygon_with_hole_and_multipolygon():
    outer, hole = box(77.0, 13.0, 77.02, 13.02), box(77.005, 13.005, 77.01, 13.01)
    result = measure(polygon(outer, hole))
    # ~2 km features: straight LAEA chords vs geodesic edges differ by O((L/R)^2) ~ 1e-7.
    assert result.area_m2 == pytest.approx(geod_area(outer) - geod_area(hole), rel=1e-7)
    assert result.geodesic_area_m2 == pytest.approx(geod_area(outer) - geod_area(hole), rel=1e-9)
    assert codes(result) == []

    second = box(77.03, 13.0, 77.04, 13.01)
    multi = {
        "type": "MultiPolygon",
        "coordinates": [[list(map(list, outer))], [list(map(list, second))]],
    }
    result = measure(multi)
    assert result.area_m2 == pytest.approx(geod_area(outer) + geod_area(second), rel=1e-7)
    assert result.geodesic_area_m2 == pytest.approx(geod_area(outer) + geod_area(second), rel=1e-9)


def test_reversed_orientation_gives_the_same_area():
    ccw = measure(polygon(box(77.0, 13.0, 77.01, 13.01)))
    cw = measure(polygon(box(77.0, 13.0, 77.01, 13.01, reverse=True)))
    assert cw.area_m2 == pytest.approx(ccw.area_m2, rel=1e-12)
    assert cw.geodesic_area_m2 == pytest.approx(ccw.geodesic_area_m2, rel=1e-12)


def test_altitude_is_dropped_with_a_warning():
    flat = box(77.0, 13.0, 77.01, 13.01)
    raised = [(x, y, 900.0 + i) for i, (x, y) in enumerate(flat)]
    raised[-1] = raised[0]
    result = measure(polygon(raised))
    assert result.area_m2 == pytest.approx(measure(polygon(flat)).area_m2, rel=1e-12)
    assert codes(result) == ["Z_DROPPED"]
    assert len(result.wgs84_geometry["coordinates"][0][0]) == 2


def test_kalianpur_input_is_measured_after_helmert_shift():
    ring = box(77.0, 13.0, 77.01, 13.01)
    to_wgs84 = Transformer.from_crs(4146, 4326, always_xy=True)
    reference = geod_area([to_wgs84.transform(x, y) for x, y in ring])
    result = measure(polygon(ring), "EPSG:4146")
    assert result.status == Status.MEASURED
    assert result.area_m2 == pytest.approx(reference, rel=1e-8)
    # Second, independent reference: the same lon/lat on Kalianpur's own ellipsoid
    # (Everest 1830, 1975 definition). The datum shift moves the polygon; its area barely changes.
    everest = CRS.from_epsg(4146).ellipsoid
    native = Geod(a=everest.semi_major_metre, rf=everest.inverse_flattening)
    lons, lats = zip(*ring, strict=True)
    assert result.area_m2 == pytest.approx(
        abs(native.polygon_area_perimeter(lons, lats)[0]), rel=1e-5
    )


# --- LAEA is equal-area, not distance-preserving ---------------------------------------


def circle(lon, lat, radius_m, step_deg=5):
    pts = [GEOD.fwd(lon, lat, az, radius_m)[:2] for az in range(0, 360, step_deg)]
    return pts + [pts[0]]


def test_long_tangential_line_is_flagged_but_area_is_not():
    ring = circle(77.0, 20.0, 1_500_000)
    as_line = measure(line(ring))
    # Tangential scale at arc c from the centre is 1/cos(c/2) ≈ 1 + c²/8; c = 1500 km / R.
    c = 1_500_000 / 6_371_000
    assert as_line.relative_difference == pytest.approx(c * c / 8, rel=0.1)
    assert "GEODESIC_DISAGREEMENT" in codes(as_line)
    assert as_line.status == Status.MEASURED  # flagged, not hidden or nulled

    as_polygon = measure(polygon(ring))
    assert as_polygon.relative_difference < 1e-5
    assert "GEODESIC_DISAGREEMENT" not in codes(as_polygon)


def test_long_radial_line_stays_within_tolerance():
    start = GEOD.fwd(77.0, 20.0, 45, -500_000)[:2]
    end = GEOD.fwd(77.0, 20.0, 45, 500_000)[:2]
    result = measure(line([start, end]))
    # Radial scale cos(c/2): mean error ≈ -c_max²/24 with c_max = 500 km / R ≈ -2.6e-4.
    assert result.relative_difference == pytest.approx((500_000 / 6_371_000) ** 2 / 24, rel=0.1)
    assert "GEODESIC_DISAGREEMENT" not in codes(result)


def test_short_line_has_no_disagreement():
    result = measure(line([(77.0, 13.0), (77.01, 13.01)]))
    assert result.relative_difference < 1e-9
    assert codes(result) == []


# --- statuses ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [77.0, 13.0]},
        {"type": "MultiPoint", "coordinates": [[1, 2], [3, 4]]},
    ],
)
def test_points_are_not_applicable(geometry):
    result = measure(geometry)
    assert result.status == Status.NOT_APPLICABLE
    assert result.area_m2 is None and result.length_m is None
    assert result.wgs84_geometry["type"] == geometry["type"]


def test_mixed_collection_is_unsupported():
    collection = {
        "type": "GeometryCollection",
        "geometries": [{"type": "Point", "coordinates": [0, 0]}, line([(0, 0), (1, 1)])],
    }
    result = measure(collection)
    assert (result.status, result.reason_code) == (Status.UNSUPPORTED_GEOMETRY, "UNSUPPORTED_TYPE")


@pytest.mark.parametrize(
    ("geometry", "code"),
    [
        (polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]), "INVALID_GEOMETRY"),  # bow-tie
        (polygon(box(0, 0, 1, 1)[:-1]), "RING_NOT_CLOSED"),
        (polygon([(0, 0), (1, 0), (0, 0)]), "RING_TOO_SHORT"),
        (polygon(box(0, 0, 1, 1), box(5, 5, 6, 6)), "INVALID_GEOMETRY"),  # hole outside shell
        (line([(0, 0)]), "LINE_TOO_SHORT"),
        ({"type": "Polygon", "coordinates": []}, None),  # no coordinates -> empty
        (line([(0, 0), ("a", 1)]), "BAD_POSITION"),
        (line([(0, 0), (float("nan"), 1)]), "NON_FINITE_COORDINATES"),
        ({"type": "Polygon", "coordinates": [[1, 2]]}, "MALFORMED_STRUCTURE"),
    ],
)
def test_invalid_geometry_is_reported_not_repaired(geometry, code):
    result = measure(geometry)
    if code is None:
        assert result.status == Status.EMPTY_GEOMETRY
        return
    assert (result.status, result.reason_code) == (Status.INVALID_GEOMETRY, code)
    assert result.area_m2 is None and result.length_m is None


def test_bow_tie_reason_comes_from_geos():
    result = measure(polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]))
    assert "Self-intersection" in result.reason


def test_null_geometry_is_empty():
    assert measure(None).status == Status.EMPTY_GEOMETRY


@pytest.mark.parametrize(
    ("issue", "status"),
    [
        ("UNSUPPORTED_GEOMETRY", Status.UNSUPPORTED_GEOMETRY),
        ("INVALID_COORDINATES", Status.INVALID_GEOMETRY),
        ("NON_FINITE_COORDINATES", Status.INVALID_GEOMETRY),
    ],
)
def test_reader_issues_map_to_statuses(issue, status):
    result = measure(None, issue_code=issue, issue_detail="detail")
    assert (result.status, result.reason_code, result.reason) == (status, issue, "detail")


def test_unknown_crs_never_produces_numbers():
    result = measure(polygon(box(77.0, 13.0, 77.01, 13.01)), crs_code=None)
    assert result.status == Status.UNKNOWN_CRS
    assert result.area_m2 is None and result.geodesic_area_m2 is None
    assert result.wgs84_geometry is None


def test_unparseable_and_untransformable_crs():
    bad = SourceCrs("INVALID", None, "garbage", "PRJ_FILE")
    ds = Dataset("SHAPEFILE", bad, [feature(polygon(box(0, 0, 1, 1)))])
    assert measure_dataset(ds).features[0].status == Status.CRS_UNSUPPORTED
    mars = measure(polygon(box(0, 0, 1, 1)), "IAU_2015:49900")
    assert (mars.status, mars.reason_code) == (Status.CRS_UNSUPPORTED, "NO_TRANSFORMATION")


def test_override_supplies_crs_for_measurement():
    ring = [
        (500_000, 1_434_000),
        (501_000, 1_434_000),
        (501_000, 1_435_000),
        (500_000, 1_435_000),
        (500_000, 1_434_000),
    ]
    result = measure(polygon(ring), crs_code=None, override="EPSG:32643")
    assert result.status == Status.MEASURED
    assert result.area_m2 == pytest.approx(1_000_800.5, abs=5)


def test_conflicting_override_fails_the_file():
    with pytest.raises(IngestionError) as exc_info:
        measure(polygon(box(0, 0, 1, 1)), "EPSG:32643", override="EPSG:4326")
    assert exc_info.value.code == "CRS_CONFLICT"


def test_failed_and_out_of_range_transformations():
    huge = measure(line([(1e12, 1e12), (1e12 + 1, 1e12)]), "EPSG:32643")
    assert (huge.status, huge.reason_code) == (Status.TRANSFORM_FAILED, "NON_FINITE_RESULT")
    assert huge.length_m is None
    bad_lat = measure(line([(10, 89), (10, 95)]))
    assert (bad_lat.status, bad_lat.reason_code) == (
        Status.TRANSFORM_FAILED,
        "COORDINATES_OUT_OF_RANGE",
    )


def test_antimeridian_crossing_is_unsupported():
    result = measure(polygon([(179, 0), (-179, 0), (-179, 1), (179, 1), (179, 0)]))
    assert (result.status, result.reason_code) == (
        Status.UNSUPPORTED_EXTENT,
        "ANTIMERIDIAN_CROSSING",
    )
    assert result.area_m2 is None


def test_hemisphere_scale_extent_is_unsupported():
    south = [(lon, -10) for lon in range(-120, 121, 60)]
    north = [(lon, 10) for lon in range(120, -121, -60)]
    result = measure(polygon(south + north + [south[0]]))
    assert (result.status, result.reason_code) == (Status.UNSUPPORTED_EXTENT, "HEMISPHERE_SCALE")


def test_per_feature_densification_cap():
    limits = MeasurementLimits(max_vertices_per_feature=10)
    result = measure(line([(0, 0), (0, 10)]), limits=limits)  # ~1106 km needs 22 inserts
    assert (result.status, result.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")


def test_file_vertex_budget_is_shared_and_does_not_block_small_features():
    long_line = feature(line([(0, 0), (0, 10)]), index=0)  # 22 inserts
    second_long = feature(line([(1, 0), (1, 10)]), index=1)  # 22 more
    small = feature(polygon(box(77.0, 13.0, 77.01, 13.01)), index=2)  # 0 inserts
    limits = MeasurementLimits(max_total_vertices=4 + 5 + 30)  # originals + room for one line
    ds = Dataset("KML", crs.kml_crs(), [long_line, second_long, small])
    first, second, third = measure_dataset(ds, limits).features
    assert first.status == Status.MEASURED and first.generated_vertices == 22
    assert (second.status, second.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")
    assert third.status == Status.MEASURED


def test_status_precedence():
    bow_tie = polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    point = {"type": "Point", "coordinates": [0, 0]}
    cases = [
        # (geometry, crs, extra, expected) — earlier rules win over later ones.
        (None, None, {"issue_code": "UNSUPPORTED_GEOMETRY"}, Status.UNSUPPORTED_GEOMETRY),
        (None, None, {}, Status.EMPTY_GEOMETRY),
        (point, None, {}, Status.NOT_APPLICABLE),  # a point needs no CRS to be not applicable
        (bow_tie, None, {}, Status.INVALID_GEOMETRY),  # geometry checks before CRS checks
        (polygon(box(0, 0, 1, 1)), None, {}, Status.UNKNOWN_CRS),
        (polygon([(179, 0), (-179, 0), (-179, 1), (179, 0)]), None, {}, Status.UNKNOWN_CRS),
    ]
    for geometry, crs_code, extra, expected in cases:
        assert measure(geometry, crs_code, **extra).status == expected, (geometry, expected)


def test_one_bad_feature_does_not_stop_the_others():
    features = [
        feature(polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]), index=0),
        feature(polygon(box(77.0, 13.0, 77.01, 13.01)), index=1),
        feature(None, index=2, issue_code="INVALID_COORDINATES"),
        feature(line([(77.0, 13.0), (77.1, 13.1)]), index=3),
    ]
    results = measure_dataset(Dataset("KML", crs.kml_crs(), features)).features
    assert [r.status for r in results] == [
        Status.INVALID_GEOMETRY,
        Status.MEASURED,
        Status.INVALID_GEOMETRY,
        Status.MEASURED,
    ]
    assert [r.index for r in results] == [0, 1, 2, 3]


def test_dataset_reports_transformation_warnings():
    ds = Dataset("SHAPEFILE", source("EPSG:4267"), [feature(polygon(box(-100, 40, -99.99, 40.01)))])
    result = measure_dataset(ds)
    assert [w["code"] for w in result.warnings] == ["NON_BEST_TRANSFORMATION"]
    assert result.features[0].status == Status.MEASURED
