import asyncio
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.middleware import BodySizeLimitMiddleware
from app.models import FeatureRecord, FileRecord, FileStatus
from app.services.storage import display_filename, save_stream
from tests.conftest import kml, shapefile_zip

KML_DOC = kml(
    """<Document><Folder><name>Ward 12</name>
    <Placemark id="p1"><name>Plot</name>
      <ExtendedData><Data name="owner"><value>Rao</value></Data></ExtendedData>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.59,12.97 77.60,12.97 77.60,12.98 77.59,12.97</coordinates></LinearRing></outerBoundaryIs>
      </Polygon></Placemark>
    <Placemark><name>Well</name><Point><coordinates>77.595,12.975</coordinates></Point></Placemark>
    </Folder></Document>"""
)


def upload(client: TestClient, name: str, data: bytes):
    return client.post("/api/files/", files={"file": (name, data, "application/octet-stream")})


def test_shapefile_upload_then_get(client, tmp_path):
    response = upload(client, "plots.zip", shapefile_zip(tmp_path))
    assert response.status_code == 201, response.text
    body = response.json()
    assert response.headers["location"] == f"/api/files/{body['id']}/"
    assert body["filename"] == "plots.zip"
    assert body["format"] == "SHAPEFILE"
    assert body["status"] == "EXTRACTED"  # Phase 1: extracted, not measured
    assert body["feature_count"] == 2
    assert body["crs"] == "EPSG:4326"
    assert body["crs_status"] == "KNOWN"
    assert body["error"] is None
    assert len(body["sha256"]) == 64

    fetched = client.get(f"/api/files/{body['id']}/")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_kml_upload_persists_features(client, settings):
    response = upload(client, "survey.kml", KML_DOC)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["format"] == "KML"
    assert body["feature_count"] == 2
    assert body["crs"] == "EPSG:4326"

    with client.app.state.session_factory() as session:
        rows = session.scalars(
            select(FeatureRecord)
            .where(FeatureRecord.file_id == body["id"])
            .order_by(FeatureRecord.index)
        ).all()
    assert [r.index for r in rows] == [0, 1]
    assert rows[0].source_id == "p1"
    assert rows[0].properties == {"name": "Plot", "owner": "Rao"}
    assert rows[0].folder_path == ["Ward 12"]
    assert rows[0].source_crs == "EPSG:4326"
    assert rows[0].source_geometry["type"] == "Polygon"
    assert rows[1].geometry_type == "Point"


def test_missing_prj_reports_unknown_crs(client, tmp_path):
    archive = shapefile_zip(tmp_path, crs=None)
    body = upload(client, "noprj.zip", archive).json()
    assert body["crs"] is None
    assert body["crs_status"] == "UNKNOWN"
    assert [w["code"] for w in body["warnings"]] == ["CRS_MISSING"]


def test_upload_is_stored_under_random_name(client, settings, tmp_path):
    body = upload(client, "../../evil name.zip", shapefile_zip(tmp_path)).json()
    assert body["filename"] == "evil name.zip"
    stored = list(settings.upload_dir.iterdir())
    assert [p.name for p in stored] == [f"{body['id']}.zip"]


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("data.txt", "UNSUPPORTED_FORMAT"),
        ("data.geojson", "UNSUPPORTED_FORMAT"),
        ("map.KMZ", "KMZ_UNSUPPORTED"),
    ],
)
def test_unsupported_formats_are_415(client, name, code):
    response = upload(client, name, b"content")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == code


def test_empty_upload_is_422(client, settings):
    response = upload(client, "empty.kml", b"")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_FILE"
    assert list(settings.upload_dir.iterdir()) == []


def test_invalid_dataset_fails_with_persisted_record(client):
    response = upload(client, "plots.zip", b"PK\x03\x04 not really a zip")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_ZIP"
    info = client.get(f"/api/files/{error['file_id']}/").json()
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "INVALID_ZIP"
    assert info["feature_count"] is None


def test_unsafe_zip_via_api(client, tmp_path):
    from tests.conftest import make_zip, write_shapefile

    parts = write_shapefile(tmp_path / "s")
    response = upload(client, "x.zip", make_zip([*parts.items(), ("../escape.txt", b"x")]))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSAFE_ARCHIVE_PATH"


def test_unsafe_xml_via_api(client):
    data = b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY e SYSTEM "file:///etc/passwd">]><kml>&e;</kml>'
    response = upload(client, "x.kml", data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNSAFE_XML"


def test_missing_file_field_is_422_envelope(client):
    response = client.post("/api/files/", data={"other": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("file_id", ["00000000-0000-0000-0000-000000000000", "not-a-uuid", "1"])
def test_unknown_id_is_404(client, file_id):
    response = client.get(f"/api/files/{file_id}/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_oversized_upload_is_413_and_nothing_stored(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", max_upload_bytes=1000, multipart_overhead_bytes=10_000
    )
    with TestClient(create_app(settings)) as client:
        # Passes the request-body guard (5 KB < 11 KB) but fails the measured file limit.
        response = upload(client, "big.kml", b"x" * 5000)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        # Fails the request-body guard before the endpoint runs.
        response = upload(client, "huge.kml", b"x" * 50_000)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        assert "1000-byte file size limit" in response.json()["error"]["message"]
        assert list(settings.upload_dir.iterdir()) == []


def test_body_guard_counts_streamed_bytes_without_content_length():
    """A chunked body without Content-Length is cut off once real bytes pass the limit."""
    reached_app = []
    received_by_app = 0

    async def app(scope, receive, send):
        nonlocal received_by_app
        reached_app.append(True)
        while True:
            message = await receive()
            received_by_app += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    chunks = [b"a" * 400] * 10
    sent = []

    async def receive():
        body = chunks.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    async def send(message):
        sent.append(message)

    guard = BodySizeLimitMiddleware(app, max_body_bytes=1000, file_limit_bytes=900)
    scope = {"type": "http", "method": "POST", "headers": []}
    asyncio.run(guard(scope, receive, send))
    assert sent[0]["status"] == 413
    assert received_by_app <= 1000


def test_save_stream_counts_actual_bytes(tmp_path):
    stored = save_stream(io.BytesIO(b"abc"), tmp_path / "f.kml", max_bytes=3)
    assert stored.size_bytes == 3
    assert stored.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    with pytest.raises(Exception) as exc_info:
        save_stream(io.BytesIO(b"abcd"), tmp_path / "g.kml", max_bytes=3)
    assert exc_info.value.code == "UPLOAD_TOO_LARGE"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.kml"]  # no partial file left


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C:\\Users\\me\\plots.zip", "plots.zip"),
        ("/tmp/a/b.kml", "b.kml"),
        ("", "upload"),
        (None, "upload"),
    ],
)
def test_display_filename(raw, expected):
    assert display_filename(raw) == expected


def test_results_survive_restart(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        file_id = upload(client, "plots.zip", shapefile_zip(tmp_path)).json()["id"]
    with TestClient(create_app(settings)) as client:
        body = client.get(f"/api/files/{file_id}/").json()
    assert body["status"] == "EXTRACTED"
    assert body["feature_count"] == 2


def test_interrupted_processing_marked_failed_on_startup(settings):
    with (
        TestClient(create_app(settings)) as client,
        client.app.state.session_factory() as session,
    ):
        session.add(
            FileRecord(
                id="11111111-1111-1111-1111-111111111111",
                original_filename="x.kml",
                format="KML",
                storage_path="x",
                size_bytes=1,
                sha256="0" * 64,
                status=FileStatus.PROCESSING,
                warnings=[],
            )
        )
        session.commit()
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/files/11111111-1111-1111-1111-111111111111/").json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == "INTERRUPTED"


def test_unexpected_error_is_sanitised_500(client, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail /etc/passwd")

    monkeypatch.setattr("app.api.files.extract", boom)
    with TestClient(client.app, raise_server_exceptions=False) as raw_client:
        response = upload(raw_client, "plots.zip", shapefile_zip(tmp_path))
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text
    with client.app.state.session_factory() as session:
        record = session.scalars(select(FileRecord)).one()
    assert record.status == "FAILED"
    assert record.error_code == "INTERNAL_ERROR"


def test_measurements_route_not_yet_implemented(client):
    # Phase 1 must not expose anything resembling measurements.
    assert (
        client.get("/api/files/00000000-0000-0000-0000-000000000000/measurements/").status_code
        == 404
    )


def test_shapefile_zip_fixture_has_real_components(tmp_path):
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(shapefile_zip(tmp_path))).namelist()
    assert {Path(n).suffix for n in names} >= {".shp", ".shx", ".dbf", ".prj"}
