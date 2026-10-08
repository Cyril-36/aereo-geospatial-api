import csv
import io
import json
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def upload(client, name):
    r = client.post("/api/files/", files={"file": (name, (SAMPLES / name).read_bytes())})
    assert r.status_code == 201
    return r.json()["id"]


def csv_rows(text):
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:])))


def test_csv_all_features_full_precision(client):
    fid = upload(client, "sample_many_parcels.zip")
    r = client.get(f"/api/files/{fid}/export/?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="sample_many_parcels-all.csv"' in r.headers["content-disposition"]
    rows = csv_rows(r.text)
    assert len(rows) == 250
    api = client.get(f"/api/files/{fid}/measurements/?limit=1000").json()["features"]
    assert [float(x["area_m2"]) for x in rows] == [f["area_m2"] for f in api]  # exact round trip


def test_filtered_export_matches_filter_count(client):
    fid = upload(client, "sample_invalid_geometry.kml")
    r = client.get(f"/api/files/{fid}/export/?format=csv&status=attention")
    assert 'filename="sample_invalid_geometry-filtered.csv"' in r.headers["content-disposition"]
    rows = csv_rows(r.text)
    assert [x["status"] for x in rows] == [
        "INVALID_GEOMETRY",
        "INVALID_GEOMETRY",
        "UNSUPPORTED_GEOMETRY",
    ]
    assert rows[0]["reason_code"] == "RING_NOT_CLOSED" and rows[0]["area_m2"] == ""
    assert rows[2]["warning_codes"] == "MULTIPLE_GEOMETRIES"


def test_json_export_is_complete_and_labelled(client):
    fid = upload(client, "sample_parcels.zip")
    body = json.loads(client.get(f"/api/files/{fid}/export/?format=json").text)
    assert body["file"]["id"] == fid
    assert body["export"]["scope"] == "all" and body["export"]["feature_count"] == 3
    assert body["export"]["units"] == {"area": "m2", "length": "m"}
    page = client.get(f"/api/files/{fid}/measurements/").json()["features"]
    assert body["features"] == page


def test_csv_formula_injection_is_neutralised(client, tmp_path):
    from tests.conftest import kml

    data = kml(
        '<Document><Placemark><name>=HYPERLINK("x")</name>'
        "<Point><coordinates>77.59,12.97</coordinates></Point></Placemark></Document>"
    )
    fid = client.post("/api/files/", files={"file": ("f.kml", data)}).json()["id"]
    rows = csv_rows(client.get(f"/api/files/{fid}/export/?format=csv").text)
    assert rows[0]["name"] == '\'=HYPERLINK("x")'


def test_unicode_filename_header(client):
    data = (SAMPLES / "sample_survey.kml").read_bytes()
    fid = client.post("/api/files/", files={"file": ("ಸರ್ವೆ.kml", data)}).json()["id"]
    cd = client.get(f"/api/files/{fid}/export/?format=json").headers["content-disposition"]
    assert "filename*=UTF-8''" in cd and 'filename="export-all.json"' in cd


@pytest.mark.parametrize("qs", ["", "format=xml", "format=csv&format=json"])
def test_export_param_errors(client, qs):
    fid = upload(client, "sample_survey.kml")
    r = client.get(f"/api/files/{fid}/export/?{qs}")
    assert r.status_code == 422


def test_export_failed_file_409(client):
    r = client.post("/api/files/", files={"file": ("x.zip", b"PK\x03\x04garbage")})
    fid = r.json()["error"]["file_id"]
    assert client.get(f"/api/files/{fid}/export/?format=csv").status_code == 409


def test_delete_during_export_keeps_export_complete(client, settings):
    from app.api.exports import export_chunks
    from app.api.params import FeatureQuery

    fid = upload(client, "sample_many_parcels.zip")
    factory = client.app.state.session_factory
    chunks = export_chunks(factory(), fid, FeatureQuery(), "csv", batch=20)
    head = next(chunks) + next(chunks)  # header + first batch: snapshot established
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    text = (head + b"".join(chunks)).decode("utf-8")
    assert len(csv_rows(text)) == 250
    assert client.get(f"/api/files/{fid}/").status_code == 404


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_delete_after_snapshot_before_first_chunk(client, fmt):
    from app.api.exports import export_chunks
    from app.api.files import _get_record
    from app.api.params import FeatureQuery

    fid = upload(client, "sample_many_parcels.zip")
    session = client.app.state.session_factory()
    _get_record(session, fid)  # endpoint preflight establishes this snapshot
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    text = b"".join(export_chunks(session, fid, FeatureQuery(), fmt, batch=20)).decode()
    assert len(csv_rows(text) if fmt == "csv" else json.loads(text)["features"]) == 250


def test_json_snapshot_survives_delete_between_batches(client):
    from app.api.exports import export_chunks
    from app.api.params import FeatureQuery

    fid = upload(client, "sample_many_parcels.zip")
    chunks = export_chunks(client.app.state.session_factory(), fid, FeatureQuery(), "json", 20)
    head = next(chunks) + next(chunks) + next(chunks)
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    body = json.loads(head + b"".join(chunks))
    assert body["export"]["feature_count"] == len(body["features"]) == 250


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1", "\n=1", "  =1"])
def test_csv_text_formula_prefixes(text):
    from app.api.exports import _text

    assert _text(text) == "'" + text


@pytest.mark.parametrize("state", ["PROCESSING", "FAILED", "EXTRACTED"])
def test_export_unready_and_missing_files(client, state):
    from app.models import FileRecord

    fid = upload(client, "sample_survey.kml")
    with client.app.state.session_factory() as session:
        session.get(FileRecord, fid).status = state
        session.commit()
    assert client.get(f"/api/files/{fid}/export/?format=json").status_code == 409
    assert client.get("/api/files/no-such-file/export/?format=json").status_code == 404
