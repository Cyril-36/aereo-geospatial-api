"""Regressions: Pagination edge cases found by driving the API."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
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


# A. offsets beyond SQLite's 64-bit integer range were a 500.
def test_huge_offset_is_an_empty_page_not_a_server_error(client):
    file_id = strict_json(
        upload(client, "p.kml", kml(f"<Placemark>{polygon(SQUARE)}</Placemark>"))
    )["id"]
    for offset in (2**63, 10**30):
        response = client.get(f"/api/files/{file_id}/measurements/?offset={offset}")
        assert response.status_code == 200, response.text
        page = strict_json(response)
        assert page["features"] == [] and page["pagination"]["next_offset"] is None


# E. A configured maximum page size below the default broke requests without `limit`.
def test_default_page_size_respects_a_smaller_configured_maximum(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", max_page_size=2)
    marks = "".join(
        f"<Placemark><Point><coordinates>77,{13 + i / 100}</coordinates></Point></Placemark>"
        for i in range(5)
    )
    with TestClient(create_app(settings)) as client:
        file_id = strict_json(upload(client, "p.kml", kml(marks)))["id"]
        response = client.get(f"/api/files/{file_id}/measurements/")
        assert response.status_code == 200, response.text
        assert strict_json(response)["pagination"]["limit"] == 2
