"""End-to-end flows through the three public endpoints with real files.

Expected measurements come from independent references: PROJ's analytic areal scale for
projected grid squares, Karney's geodesics (pyproj.Geod) on the test's own vertices, and the
published WGS84 meridian arc.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from pyproj import CRS, Transformer
from pyproj.transformer import TransformerGroup

from app.config import Settings
from app.main import create_app
from tests.e2e_helpers import (
    US_FOOT,
    geodesic_area,
    geodesic_square,
    kml_coords,
    polygon_zip,
    projected_ground_area,
    square,
    strict_json,
    upload,
)

UTM_A = square(500_000, 1_434_000, 1000)  # on the central meridian of zone 43N
UTM_B = square(825_000, 1_434_000, 1000)  # near 78°E, where UTM scale is ~1.0009


def utm_zip(tmp_path, crs="EPSG:32643"):
    return polygon_zip(
        tmp_path,
        [
            (UTM_A, {"plot_id": "P-17", "owner": "Rao"}),
            (UTM_B, {"plot_id": "P-18", "owner": "Iyer"}),
        ],
        crs,
    )


SURVEY_SQUARE = geodesic_square(77.59, 12.97, 1000)
BOW_TIE = [(77.6, 12.9), (77.61, 12.91), (77.61, 12.9), (77.6, 12.91), (77.6, 12.9)]
SURVEY_KML = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Field visit</name>
  <Folder><name>Ward 12</name>
    <Placemark id="farm-1"><name>Farm 1</name>
      <ExtendedData>
        <Data name="owner"><value>Rao</value></Data>
        <SchemaData schemaUrl="#s"><SimpleData name="survey_no">17</SimpleData></SchemaData>
      </ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        {kml_coords([(x, y) for x, y in SURVEY_SQUARE])}
      </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark id="canal"><name>Canal</name>
      <LineString><coordinates>0,0,5 0,1,5</coordinates></LineString></Placemark>
    <Placemark id="well"><name>Borewell</name>
      <Point><coordinates>77.595,12.975</coordinates></Point></Placemark>
    <Placemark id="bad"><name>Bow tie</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>{kml_coords(BOW_TIE)}</coordinates>
      </LinearRing></outerBoundaryIs></Polygon></Placemark>
  </Folder></Document></kml>""".encode()


def by_index(page: dict) -> dict[int, dict]:
    return {f["index"]: f for f in page["features"]}


# 1. Valid polygon Shapefile -------------------------------------------------------------


def test_utm_shapefile_end_to_end(client, tmp_path):
    response = upload(client, "parcels.zip", utm_zip(tmp_path))
    assert response.status_code == 201
    info = strict_json(response)
    assert response.headers["location"] == f"/api/files/{info['id']}/"
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == 2
    assert info["counts"] == {"MEASURED": 2}
    assert (info["crs"], info["crs_status"], info["crs_origin"]) == (
        "EPSG:32643",
        "KNOWN",
        "PRJ_FILE",
    )
    assert "UTM zone 43N" in info["transformation"]["operation"]
    assert info["error"] is None and info["processed_at"] is not None
    assert strict_json(client.get(f"/api/files/{info['id']}/")) == info

    page = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))
    assert page["counts"] == {"MEASURED": 2}
    assert page["pagination"] == {
        "limit": 100,
        "offset": 0,
        "total": 2,
        "returned": 2,
        "next_offset": None,
    }
    a, b = page["features"]
    # 9. UTM metre-based measurement against PROJ's areal scale (~1,000,800 and ~998,181 m²).
    assert a["area_m2"] == pytest.approx(projected_ground_area("EPSG:32643", UTM_A, 1000), rel=1e-6)
    assert b["area_m2"] == pytest.approx(projected_ground_area("EPSG:32643", UTM_B, 1000), rel=1e-6)
    assert a["area_m2"] == pytest.approx(1_000_800.5, abs=5)
    assert b["area_m2"] == pytest.approx(998_181, abs=5)
    assert a["length_m"] is None
    assert a["properties"] == {"plot_id": "P-17", "owner": "Rao"}
    assert (a["status"], a["measurement_method"]) == ("MEASURED", "LOCAL_LAEA")
    assert a["measurement_crs"].startswith("+proj=laea")
    # 10. Geometry is the transformed copy, labelled as such; the source CRS is kept.
    assert (a["geometry_crs"], a["geometry_origin"], a["source_crs"]) == (
        "EPSG:4326",
        "TRANSFORMED",
        "EPSG:32643",
    )
    lon, lat = a["geometry"]["coordinates"][0][0]
    assert lon == pytest.approx(75.0, abs=0.01) and lat == pytest.approx(12.97, abs=0.01)
    assert a["geodesic_reference"]["area_m2"] == pytest.approx(a["area_m2"], rel=1e-8)
    assert a["transformation"] == info["transformation"]
    assert isinstance(a["generated_vertices"], int)


# 2-6. KML with polygon, line, point and an invalid polygon -------------------------------


def test_kml_end_to_end(client):
    response = upload(client, "survey.kml", SURVEY_KML)
    assert response.status_code == 201
    info = strict_json(response)
    assert info["counts"] == {"INVALID_GEOMETRY": 1, "MEASURED": 2, "NOT_APPLICABLE": 1}
    assert (info["crs"], info["crs_origin"]) == ("EPSG:4326", "KML_SPECIFICATION")

    features = by_index(strict_json(client.get(f"/api/files/{info['id']}/measurements/")))
    farm, canal, well, bad = features[0], features[1], features[2], features[3]

    # 3. Area against Karney's geodesic area; length against the published meridian arc.
    assert farm["area_m2"] == pytest.approx(geodesic_area(SURVEY_SQUARE), rel=1e-8)
    assert canal["length_m"] == pytest.approx(110_574.389, rel=1e-5)
    assert canal["generated_vertices"] == 2
    assert [w["code"] for w in canal["warnings"]] == ["Z_DROPPED"]
    # 4. The point is not applicable, but keeps its geometry.
    assert (well["status"], well["area_m2"], well["length_m"]) == ("NOT_APPLICABLE", None, None)
    assert well["geometry"] == {"type": "Point", "coordinates": [77.595, 12.975]}
    # 5. The invalid polygon is reported, not repaired, and did not stop the others.
    assert (bad["status"], bad["reason_code"]) == ("INVALID_GEOMETRY", "INVALID_GEOMETRY")
    assert "Self-intersection" in bad["reason"]
    assert bad["area_m2"] is None and bad["geodesic_reference"] is None
    assert (bad["geometry_origin"], bad["geometry_crs"]) == ("SOURCE", "EPSG:4326")
    assert bad["transformation"] is None
    # 6. ExtendedData, SchemaData and folder paths survive.
    assert farm["properties"] == {"name": "Farm 1", "owner": "Rao", "survey_no": "17"}
    assert farm["folder_path"] == ["Field visit", "Ward 12"]
    assert farm["source_id"] == "farm-1"


# 7-8. Missing .prj and the source_crs field --------------------------------------------


def test_missing_prj_is_unknown_and_unmeasured(client, tmp_path):
    info = strict_json(upload(client, "noprj.zip", utm_zip(tmp_path, crs=None)))
    assert info["status"] == "COMPLETED"
    assert info["counts"] == {"UNKNOWN_CRS": 2}
    assert (info["crs"], info["crs_status"], info["transformation"]) == (None, "UNKNOWN", None)
    assert [w["code"] for w in info["warnings"]] == ["CRS_MISSING"]
    for f in strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"]:
        assert (f["status"], f["area_m2"], f["geodesic_reference"]) == ("UNKNOWN_CRS", None, None)
        # Original coordinates, never labelled as WGS84.
        assert (f["geometry_origin"], f["geometry_crs"]) == ("SOURCE", None)
        assert f["geometry"]["coordinates"][0][0][0] >= 500_000


@pytest.mark.parametrize("source_crs", ["EPSG:32643", "32643", CRS.from_epsg(32643).to_wkt()])
def test_source_crs_supplies_a_missing_crs(client, tmp_path, source_crs):
    info = strict_json(upload(client, "noprj.zip", utm_zip(tmp_path, crs=None), source_crs))
    assert (info["crs"], info["crs_origin"], info["counts"]) == (
        "EPSG:32643",
        "OVERRIDE",
        {"MEASURED": 2},
    )
    a = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"][0]
    assert a["area_m2"] == pytest.approx(projected_ground_area("EPSG:32643", UTM_A, 1000), rel=1e-6)


def test_matching_source_crs_is_accepted(client, tmp_path):
    info = strict_json(upload(client, "parcels.zip", utm_zip(tmp_path), "EPSG:32643"))
    assert (info["status"], info["crs_origin"]) == ("COMPLETED", "PRJ_FILE")


# 9. State Plane in US survey feet ------------------------------------------------------


def test_state_plane_feet_end_to_end(client, tmp_path):
    side_ft = 1000 / US_FOOT  # a 1000 m grid square expressed in US survey feet
    origin = Transformer.from_crs(4326, 2263, always_xy=True).transform(-73.985, 40.758)
    ring = square(origin[0], origin[1], side_ft)
    info = strict_json(
        upload(
            client,
            "nyc.zip",
            polygon_zip(tmp_path, [(ring, {"plot_id": "M1", "owner": "x"})], "EPSG:2263"),
        )
    )
    assert info["crs"] == "EPSG:2263" and info["counts"] == {"MEASURED": 1}
    expected_warning = not TransformerGroup("EPSG:2263", "EPSG:4326").best_available
    assert ("NON_BEST_TRANSFORMATION" in [w["code"] for w in info["warnings"]]) is expected_warning
    assert info["transformation"]["accuracy_m"] is not None
    f = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"][0]
    # The 1000 m grid square's ground area from the projection's areal scale (~1e6 m²,
    # not 1000² ft² = 92,903 m², and not 1e6 ft²).
    assert f["area_m2"] == pytest.approx(projected_ground_area("EPSG:2263", ring, 1000), rel=1e-6)
    assert 990_000 < f["area_m2"] < 1_010_000


# 11. Restart -----------------------------------------------------------------------------


def test_results_survive_restart(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        file_id = strict_json(upload(client, "survey.kml", SURVEY_KML))["id"]
        before = strict_json(client.get(f"/api/files/{file_id}/measurements/"))
        info_before = strict_json(client.get(f"/api/files/{file_id}/"))
    with TestClient(create_app(settings)) as client:
        assert strict_json(client.get(f"/api/files/{file_id}/measurements/")) == before
        assert strict_json(client.get(f"/api/files/{file_id}/")) == info_before


# 10. Serialization -------------------------------------------------------------------------


def test_null_and_non_finite_attributes_serialize_as_strict_json(client, tmp_path):
    schema = {"geometry": "Polygon", "properties": {"note": "str", "value": "float"}}
    records = [
        {
            "geometry": {"type": "Polygon", "coordinates": [square(77, 13, 0.01)]},
            "properties": {"note": None, "value": float("nan")},
        },
    ]
    from tests.conftest import make_zip, write_shapefile

    files = write_shapefile(tmp_path / "n", name="n", schema=schema, records=records)
    response = upload(client, "n.zip", make_zip(files.items()))
    page = strict_json(client.get(f"/api/files/{strict_json(response)['id']}/measurements/"))
    assert page["features"][0]["properties"] == {"note": None, "value": None}


def test_reader_and_measurement_warnings_are_both_kept(client):
    kml = (
        b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>'
        b'<name>Plot</name><ExtendedData><Data name="name"><value>Survey name</value></Data>'
        b"</ExtendedData><LineString><coordinates>77,13,900 77.01,13,905</coordinates>"
        b"</LineString></Placemark></kml>"
    )
    info = strict_json(upload(client, "w.kml", kml))
    feature = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"][0]
    # Reader warning first, then the measurement warning.
    assert [w["code"] for w in feature["warnings"]] == ["PROPERTY_KEY_COLLISION", "Z_DROPPED"]
    assert feature["properties"] == {"name": "Plot", "name__2": "Survey name"}


def test_sensitive_property_values_are_not_logged(client, caplog):
    marker = "OWNER-MARKER-9431"
    kml = SURVEY_KML.replace(b"<value>Rao</value>", f"<value>{marker}</value>".encode())
    with caplog.at_level(logging.DEBUG):
        assert upload(client, "survey.kml", kml).status_code == 201
    assert caplog.records  # something was logged ...
    assert all(marker not in r.getMessage() for r in caplog.records)  # ... but not the value


def test_settings_limits_reach_the_engine(tmp_path):
    # A 10 km densification step makes the 110.6 km canal need 11 inserts (13 vertices);
    # with a 6-vertex per-feature limit only the canal is refused (the polygon has 5).
    settings = Settings(
        data_dir=tmp_path / "d", densify_max_segment_m=10_000, max_vertices_per_feature=6
    )
    with TestClient(create_app(settings)) as client:
        info = strict_json(upload(client, "survey.kml", SURVEY_KML))
        features = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"]
    assert (features[1]["status"], features[1]["reason_code"]) == (
        "UNSUPPORTED_EXTENT",
        "DENSIFICATION_LIMIT",
    )
    assert features[0]["status"] == "MEASURED"
    assert info["counts"]["UNSUPPORTED_EXTENT"] == 1
