"""Regressions: Placemark geometry handling found by driving the API."""

from tests.e2e_helpers import strict_json, upload

KML_HEAD = '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
SQUARE = "77,13 77.01,13 77.01,13.01 77,13.01 77,13"
FAR_SQUARE = "78,13 78.01,13 78.01,13.01 78,13.01 78,13"


def kml(body: str) -> bytes:
    return f"{KML_HEAD}{body}</Document></kml>".encode()


def polygon(ring: str) -> str:
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon>"
    )


def first_feature(client, data: bytes) -> tuple[dict, dict]:
    info = strict_json(upload(client, "f.kml", data))
    return info, strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"][0]


# B. A Placemark with several geometries silently kept only the first one.
def test_placemark_with_several_geometries_loses_nothing(client):
    body = (
        f"<Placemark><Point><coordinates>77,13</coordinates></Point>{polygon(SQUARE)}</Placemark>"
    )
    info, feature = first_feature(client, kml(body))
    assert feature["geometry_type"] == "GeometryCollection"
    assert len(feature["geometry"]["geometries"]) == 2
    assert feature["status"] == "UNSUPPORTED_GEOMETRY"
    assert "MULTIPLE_GEOMETRIES" in [w["code"] for w in feature["warnings"]]


def test_placemark_with_several_polygons_measures_all_of_them(client):
    body = f"<Placemark>{polygon(SQUARE)}{polygon(FAR_SQUARE)}</Placemark>"
    _, feature = first_feature(client, kml(body))
    single = first_feature(client, kml(f"<Placemark>{polygon(SQUARE)}</Placemark>"))[1]
    assert feature["geometry_type"] == "MultiPolygon" and feature["status"] == "MEASURED"
    assert feature["area_m2"] > 1.9 * single["area_m2"]
    assert "MULTIPLE_GEOMETRIES" in [w["code"] for w in feature["warnings"]]


# C. Extra LinearRings inside outerBoundaryIs were silently dropped.
def test_several_rings_in_one_outer_boundary_are_invalid_not_dropped(client):
    body = (
        "<Placemark><Polygon><outerBoundaryIs>"
        f"<LinearRing><coordinates>{SQUARE}</coordinates></LinearRing>"
        f"<LinearRing><coordinates>{FAR_SQUARE}</coordinates></LinearRing>"
        "</outerBoundaryIs></Polygon></Placemark>"
    )
    _, feature = first_feature(client, kml(body))
    assert (feature["status"], feature["reason_code"]) == (
        "INVALID_GEOMETRY",
        "INVALID_COORDINATES",
    )
    assert feature["area_m2"] is None
