"""Existing requests must keep byte-identical responses (ids and timestamps normalised).

Snapshots were recorded before the web workspace was added. Regenerate them only for a
deliberate, documented API change: AEREO_WRITE_GOLDEN=1 uv run pytest tests/test_golden_compat.py
"""

import os
import re
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
SAMPLES = Path(__file__).resolve().parent.parent / "samples"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?")


def normalized(text: str) -> str:
    return STAMP.sub("<TIME>", UUID.sub("<ID>", text))


CASES = [
    ("parcels_upload", "sample_parcels.zip", None, ""),
    ("parcels_measurements", "sample_parcels.zip", None, "measurements/"),
    ("parcels_page", "sample_parcels.zip", None, "measurements/?limit=2&offset=1"),
    ("survey_measurements", "sample_survey.kml", None, "measurements/"),
    ("missing_crs_info", "sample_missing_crs.zip", None, ""),
    ("override_measurements", "sample_missing_crs.zip", "EPSG:32643", "measurements/"),
]


@pytest.mark.parametrize(("name", "sample", "crs", "path"), CASES)
def test_existing_responses_unchanged(client, name, sample, crs, path):
    data = {"source_crs": crs} if crs else None
    files = {"file": (sample, (SAMPLES / sample).read_bytes())}
    upload = client.post("/api/files/", files=files, data=data)
    assert upload.status_code == 201
    response = client.get(f"/api/files/{upload.json()['id']}/{path}")
    actual = f"{response.status_code}\n{response.headers['content-type']}\n"
    actual += normalized(response.text)
    snapshot = GOLDEN / f"{name}.txt"
    if os.environ.get("AEREO_WRITE_GOLDEN"):
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(actual)
    assert actual == snapshot.read_text()


def test_upload_response_unchanged(client):
    sample = "sample_parcels.zip"
    response = client.post("/api/files/", files={"file": (sample, (SAMPLES / sample).read_bytes())})
    actual = f"{response.status_code}\n{response.headers['location'] != ''}\n"
    actual += normalized(response.text)
    snapshot = GOLDEN / "parcels_post.txt"
    if os.environ.get("AEREO_WRITE_GOLDEN"):
        snapshot.write_text(actual)
    assert actual == snapshot.read_text()


def test_error_bodies_unchanged(client):
    assert client.get("/api/files/not-a-uuid/").json() == {
        "error": {"code": "FILE_NOT_FOUND", "message": "No file with id 'not-a-uuid'."}
    }
    assert client.get("/api/nope").json()["error"]["code"] == "NOT_FOUND"
