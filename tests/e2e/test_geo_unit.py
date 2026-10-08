"""plotCheck: which geometries the map may draw, run in the browser on the real module."""

import pytest

pytestmark = pytest.mark.e2e

CASES = [
    ({"geometry": None, "geometry_crs": None}, "no geometry"),
    (
        {
            "geometry": {"type": "Point", "coordinates": [500000, 1400000]},
            "geometry_crs": "EPSG:32643",
        },
        "coordinates are in EPSG:32643, not latitude/longitude",
    ),
    (
        {"geometry": {"type": "Point", "coordinates": [1, 2]}, "geometry_crs": None},
        "coordinate system unknown",
    ),
    (
        {"geometry": {"type": "Point", "coordinates": [200, 0]}, "geometry_crs": "EPSG:4326"},
        "coordinates out of range",
    ),
    (
        {"geometry": {"type": "Point", "coordinates": ["x", 0]}, "geometry_crs": "EPSG:4326"},
        "coordinates out of range",
    ),
    (
        {"geometry": {"type": "LineString", "coordinates": [[1, 2]]}, "geometry_crs": "EPSG:4326"},
        "unsupported shape",
    ),
    (
        {
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]},
            "geometry_crs": "EPSG:4326",
        },
        "unsupported shape",
    ),
    (
        {"geometry": {"type": "GeometryCollection", "geometries": []}, "geometry_crs": "EPSG:4326"},
        "unsupported shape",
    ),
    (
        {"geometry": {"type": "Track", "coordinates": []}, "geometry_crs": "EPSG:4326"},
        "unsupported shape",
    ),
]
HOLE = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]], [[1, 1], [2, 1], [2, 2], [1, 2], [1, 1]]],
        [[[5, 5], [6, 5], [6, 6], [5, 5]]],
    ],
}


@pytest.fixture
def check(page_ok, server):
    page_ok.goto(server.base_url + "/")

    def run(feature):
        return page_ok.evaluate(
            "async (f) => (await import('/static/js/geo.js')).plotCheck(f)", feature
        )

    return run


@pytest.mark.parametrize(("feature", "reason"), CASES)
def test_rejected_geometries(check, feature, reason):
    assert check(feature) == {"ok": False, "reason": reason}


def test_accepted_multipolygon_with_hole_counts_vertices(check):
    assert check({"geometry": HOLE, "geometry_crs": "EPSG:4326"}) == {"ok": True, "vertices": 14}


def test_accepted_collection(check):
    geometry = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [77.59, 12.97]},
            {"type": "LineString", "coordinates": [[77.59, 12.97], [77.6, 12.98]]},
        ],
    }
    assert check({"geometry": geometry, "geometry_crs": "EPSG:4326"}) == {"ok": True, "vertices": 3}
