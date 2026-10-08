"""Repeated multipart fields are rejected, never resolved silently (last value wins)."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import FileRecord
from tests.e2e_helpers import strict_json

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
KML = (SAMPLES / "sample_survey.kml").read_bytes()
PARCELS = (SAMPLES / "sample_parcels.zip").read_bytes()
NO_PRJ = (SAMPLES / "sample_missing_crs.zip").read_bytes()


def post(client, parts: list[tuple[str, tuple]]):
    return client.post("/api/files/", files=parts)


def file_part(name: str, data: bytes) -> tuple[str, tuple]:
    return ("file", (name, data, "application/octet-stream"))


def crs_part(value: str) -> tuple[str, tuple]:
    return ("source_crs", (None, value))


def assert_rejected(response, field: str, settings, client) -> None:
    assert response.status_code == 422, response.text
    body = strict_json(response)
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "DUPLICATE_FORM_FIELD"
    assert f"'{field}'" in body["error"]["message"]
    # Validated before anything is stored: no record, no upload on disk.
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FileRecord)) == 0
    assert not settings.upload_dir.exists() or list(settings.upload_dir.iterdir()) == []


def test_two_different_files_are_rejected(client, settings):
    response = post(client, [file_part("a.kml", KML), file_part("b.zip", PARCELS)])
    assert_rejected(response, "file", settings, client)


def test_the_same_file_twice_is_rejected(client, settings):
    response = post(client, [file_part("a.kml", KML), file_part("a.kml", KML)])
    assert_rejected(response, "file", settings, client)


def test_conflicting_source_crs_values_are_rejected(client, settings):
    parts = [file_part("p.zip", NO_PRJ), crs_part("EPSG:32643"), crs_part("EPSG:4326")]
    assert_rejected(post(client, parts), "source_crs", settings, client)


def test_identical_repeated_source_crs_is_rejected(client, settings):
    parts = [file_part("p.zip", NO_PRJ), crs_part("EPSG:32643"), crs_part("EPSG:32643")]
    assert_rejected(post(client, parts), "source_crs", settings, client)


def test_repeated_empty_source_crs_is_rejected(client, settings):
    parts = [file_part("p.zip", NO_PRJ), crs_part(""), crs_part("")]
    assert_rejected(post(client, parts), "source_crs", settings, client)


@pytest.mark.parametrize(
    ("parts", "counts", "origin"),
    [
        ([file_part("s.kml", KML)], {"MEASURED": 2, "NOT_APPLICABLE": 1}, "KML_SPECIFICATION"),
        ([file_part("p.zip", NO_PRJ), crs_part("EPSG:32643")], {"MEASURED": 3}, "OVERRIDE"),
        ([file_part("p.zip", NO_PRJ)], {"UNKNOWN_CRS": 3}, "NONE"),
    ],
)
def test_single_fields_still_work(client, parts, counts, origin):
    response = post(client, parts)
    assert response.status_code == 201, response.text
    info = strict_json(response)
    assert (info["status"], info["counts"], info["crs_origin"]) == ("COMPLETED", counts, origin)


def test_openapi_still_documents_the_form_fields(client):
    schema = client.get("/openapi.json").json()
    ref = schema["paths"]["/api/files/"]["post"]["requestBody"]["content"]["multipart/form-data"]
    name = ref["schema"]["$ref"].rsplit("/", 1)[-1]
    properties = schema["components"]["schemas"][name]["properties"]
    assert set(properties) == {"file", "source_crs"}
    assert schema["components"]["schemas"][name]["required"] == ["file"]
