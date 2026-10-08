"""Empty means no coordinate value at all; malformed values are invalid, never 'empty'."""

import pytest

from app.services.crs import kml_crs
from app.services.measurements import Status
from tests.helpers_measure import feature, line, run


@pytest.mark.parametrize(
    ("geometry", "code"),
    [
        (line([("a", "b"), ("c", "d")]), "BAD_POSITION"),
        ({"type": "LineString", "coordinates": [[None, None]]}, "BAD_POSITION"),
        ({"type": "LineString", "coordinates": [[None, None], [None, None]]}, "BAD_POSITION"),
        ({"type": "LineString", "coordinates": ["x", "y"]}, "MALFORMED_STRUCTURE"),
        (
            {"type": "Polygon", "coordinates": [[["a", "b"], ["c", "d"], ["e", "f"], ["a", "b"]]]},
            "BAD_POSITION",
        ),
        ({"type": "Polygon", "coordinates": [None]}, "MALFORMED_STRUCTURE"),
        ({"type": "Point", "coordinates": ["x", "y"]}, "BAD_POSITION"),
        ({"type": "Point", "coordinates": [None, None]}, "BAD_POSITION"),
    ],
)
def test_entirely_malformed_coordinates_are_invalid_not_empty(geometry, code):
    result = run([feature(geometry)], kml_crs())[0]
    assert (result.status, result.reason_code) == (Status.INVALID_GEOMETRY, code)
    assert result.area_m2 is None and result.length_m is None


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {"type": "LineString"},
        {"type": "LineString", "coordinates": None},
        {"type": "LineString", "coordinates": []},
        {"type": "Polygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[]]},
        {"type": "MultiPolygon", "coordinates": [[[]]]},
        {"type": "GeometryCollection", "geometries": []},
    ],
)
def test_absent_or_empty_coordinates_are_empty(geometry):
    assert run([feature(geometry)], kml_crs())[0].status == Status.EMPTY_GEOMETRY


def test_reader_issue_still_wins_over_malformed_coordinates():
    malformed = line([("a", "b"), ("c", "d")])
    result = run([feature(malformed, issue_code="INVALID_COORDINATES")], kml_crs())[0]
    assert (result.status, result.reason_code) == (Status.INVALID_GEOMETRY, "INVALID_COORDINATES")


def test_non_empty_collection_is_still_unsupported():
    collection = {
        "type": "GeometryCollection",
        "geometries": [{"type": "Point", "coordinates": [0, 0]}],
    }
    assert run([feature(collection)], kml_crs())[0].status == Status.UNSUPPORTED_GEOMETRY


def test_malformed_feature_does_not_stop_valid_ones():
    results = run(
        [feature(line([("a", "b")]), 0), feature(line([(77.0, 13.0), (77.01, 13.0)]), 1)],
        kml_crs(),
    )
    assert [r.status for r in results] == [Status.INVALID_GEOMETRY, Status.MEASURED]
