"""The workspace's verification samples give exactly the results the README documents."""

from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def upload_sample(client, name):
    files = {"file": (name, (SAMPLES / name).read_bytes())}
    response = client.post("/api/files/", files=files)
    assert response.status_code == 201, response.text
    info = response.json()
    page = client.get(f"/api/files/{info['id']}/measurements/?limit=1000").json()
    return info, page["features"]


def test_invalid_geometry_sample(client):
    info, features = upload_sample(client, "sample_invalid_geometry.kml")
    got = [
        (f["properties"]["name"], f["status"], f["reason_code"], [w["code"] for w in f["warnings"]])
        for f in features
    ]
    assert got == [
        ("Valid field", "MEASURED", None, []),
        ("Unclosed plot", "INVALID_GEOMETRY", "RING_NOT_CLOSED", []),
        ("Bow-tie boundary", "INVALID_GEOMETRY", "INVALID_GEOMETRY", []),
        ("Pump house", "UNSUPPORTED_GEOMETRY", "UNSUPPORTED_TYPE", ["MULTIPLE_GEOMETRIES"]),
        ("Borewell", "NOT_APPLICABLE", "POINT_GEOMETRY", []),
    ]
    assert info["counts"] == {
        "INVALID_GEOMETRY": 2,
        "MEASURED": 1,
        "NOT_APPLICABLE": 1,
        "UNSUPPORTED_GEOMETRY": 1,
    }


def test_many_parcels_sample_spans_pages(client):
    info, features = upload_sample(client, "sample_many_parcels.zip")
    assert info["feature_count"] == 250
    assert info["counts"] == {"MEASURED": 250}
    assert info["crs"] == "EPSG:32643"
    assert len({f["properties"]["parcel_id"] for f in features}) == 250
    uses = [f["properties"]["land_use"] for f in features]
    assert uses.count("industrial") == 83
