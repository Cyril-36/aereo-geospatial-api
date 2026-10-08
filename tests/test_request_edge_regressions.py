"""Request-level edge cases found by driving the API: every rejection uses the documented
statuses and envelope, nothing ambiguous is resolved silently."""

from pathlib import Path

import pytest

from tests.e2e_helpers import strict_json, upload

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
KML = (SAMPLES / "sample_survey.kml").read_bytes()


def envelope(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = strict_json(response)
    assert set(body) == {"error"} and body["error"]["code"] == code
    return body["error"]


# G. Multipart parsing failures were 400 HTTP_ERROR, outside the documented contract.
def test_oversized_form_field_is_413(client):
    response = client.post(
        "/api/files/",
        files={"file": ("s.kml", KML)},
        data={"source_crs": "A" * (1100 * 1024)},  # over Starlette's 1 MiB per-field limit
    )
    error = envelope(response, 413, "FORM_LIMIT_EXCEEDED")
    assert "maximum size" in error["message"]


def test_too_many_form_fields_is_413(client):
    data = {f"x{i}": "v" for i in range(1001)}
    response = client.post("/api/files/", files={"file": ("s.kml", KML)}, data=data)
    envelope(response, 413, "FORM_LIMIT_EXCEEDED")


def test_malformed_multipart_body_is_422(client):
    response = client.post(
        "/api/files/",
        content=b"garbage-without-boundary",
        headers={"Content-Type": "multipart/form-data; boundary=abc"},
    )
    envelope(response, 422, "MALFORMED_REQUEST")


# H. Repeated query parameters were silently last-wins.
@pytest.mark.parametrize("query", ["limit=1&limit=3", "offset=0&offset=2", "limit=2&limit=2"])
def test_repeated_pagination_parameters_are_rejected(client, query):
    file_id = strict_json(upload(client, "s.kml", KML))["id"]
    error = envelope(
        client.get(f"/api/files/{file_id}/measurements/?{query}"), 422, "DUPLICATE_QUERY_PARAMETER"
    )
    assert query.split("=")[0] in error["message"]


def test_single_pagination_parameters_still_work(client):
    file_id = strict_json(upload(client, "s.kml", KML))["id"]
    page = strict_json(client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=1"))
    assert page["pagination"]["limit"] == 1 and page["features"][0]["index"] == 1


# I. A file named exactly ".kml" had no suffix (Python treats it as a hidden file).
@pytest.mark.parametrize("name", [".kml", ".KML"])
def test_extension_only_file_name_is_accepted(client, name):
    info = strict_json(upload(client, name, KML))
    assert (info["status"], info["format"]) == ("COMPLETED", "KML")
