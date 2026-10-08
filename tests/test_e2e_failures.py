"""End-to-end failure paths: legacy data, injected persistence failures, interruption,
pagination limits, error envelopes and corrupted uploads. A failure must never leave a
COMPLETED file or a partial set of features."""

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.config import Settings
from app.main import create_app
from app.models import FeatureRecord, FileRecord
from tests.conftest import make_zip, write_shapefile
from tests.e2e_helpers import polygon_zip, square, strict_json, upload

PHASE1_DUMP = Path(__file__).parent / "fixtures" / "phase1_database.sql"
KML_HEAD = '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'


def points_kml(count: int) -> bytes:
    marks = "".join(
        f'<Placemark id="p{i}"><name>Point {i}</name>'
        f"<Point><coordinates>{77 + i / 1000},13</coordinates></Point></Placemark>"
        for i in range(count)
    )
    return f"{KML_HEAD}{marks}</Document></kml>".encode()


def lines_kml() -> bytes:
    return (
        f"{KML_HEAD}<Placemark><LineString><coordinates>77,13 77.01,13</coordinates>"
        "</LineString></Placemark></Document></kml>"
    ).encode()


def assert_envelope(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = strict_json(response)
    assert set(body) == {"error"}
    assert set(body["error"]) <= {"code", "message", "file_id"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    return body["error"]


def stored(app, file_id: str) -> tuple[FileRecord, int]:
    with app.state.session_factory() as session:
        record = session.get(FileRecord, file_id)
        features = session.scalar(
            select(func.count()).select_from(FeatureRecord).where(FeatureRecord.file_id == file_id)
        )
        return record, features


def only_file(app) -> FileRecord:
    with app.state.session_factory() as session:
        return session.scalars(select(FileRecord)).one()


# 12. Phase 1 database through the application ------------------------------------------


def test_phase1_database_is_served_after_upgrade(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.data_dir.mkdir()
    connection = sqlite3.connect(settings.data_dir / "aereo.db")
    connection.executescript(PHASE1_DUMP.read_text())
    legacy = dict(connection.execute("SELECT original_filename, id FROM files").fetchall())
    connection.close()

    with TestClient(create_app(settings)) as client:
        kml = strict_json(client.get(f"/api/files/{legacy['field.kml']}/"))
        assert (kml["status"], kml["feature_count"], kml["counts"]) == ("EXTRACTED", 3, None)
        assert kml["crs"] == "EPSG:4326"
        error = assert_envelope(
            client.get(f"/api/files/{legacy['field.kml']}/measurements/"),
            409,
            "MEASUREMENTS_UNAVAILABLE",
        )
        assert "Upload it again" in error["message"]
        assert_envelope(
            client.get(f"/api/files/{legacy['broken.zip']}/measurements/"), 409, "FILE_FAILED"
        )
        # New uploads work alongside the legacy rows.
        assert strict_json(upload(client, "l.kml", lines_kml()))["status"] == "COMPLETED"

    connection = sqlite3.connect(settings.data_dir / "aereo.db")
    assert connection.execute("SELECT count(*) FROM features").fetchone()[0] == 7 + 1
    assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(1,), (2,), (3,)]
    connection.close()


# 13. Injected failures never produce COMPLETED or partial features ---------------------


@pytest.fixture
def raw_client(settings):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_failure_while_building_results(raw_client, monkeypatch):
    import app.api.files as files_api

    real = files_api.apply_results

    def apply_then_fail(record, processed):
        real(record, processed)  # the record and its features are fully populated ...
        raise RuntimeError("simulated failure after building results")  # ... then it fails

    monkeypatch.setattr(files_api, "apply_results", apply_then_fail)
    error = assert_envelope(upload(raw_client, "p.kml", points_kml(3)), 500, "INTERNAL_ERROR")
    assert "request id" in error["message"] and "simulated" not in error["message"]
    record = only_file(raw_client.app)
    _, features = stored(raw_client.app, record.id)
    assert (record.status, record.error_code, features) == ("FAILED", "PERSISTENCE_FAILED", 0)
    assert record.status_counts is None
    assert_envelope(raw_client.get(f"/api/files/{record.id}/measurements/"), 409, "FILE_FAILED")


def test_database_failure_while_writing_features(raw_client):
    factory = raw_client.app.state.session_factory

    def reject_features(session, _context, _instances):
        if any(isinstance(obj, FeatureRecord) for obj in session.new):
            raise RuntimeError("simulated database failure")

    event.listen(factory, "before_flush", reject_features)
    try:
        assert_envelope(upload(raw_client, "p.kml", points_kml(3)), 500, "INTERNAL_ERROR")
    finally:
        event.remove(factory, "before_flush", reject_features)
    record = only_file(raw_client.app)
    _, features = stored(raw_client.app, record.id)
    assert (record.status, record.error_code, features) == ("FAILED", "PERSISTENCE_FAILED", 0)


def test_failure_that_also_blocks_recording_the_failure(settings):
    """If even marking FAILED is impossible, the row stays PROCESSING (never COMPLETED) and
    startup recovery marks it INTERRUPTED."""
    app = create_app(settings)
    factory = app.state.session_factory
    blocked = {"on": False}

    def fail_everything(session, _context, _instances):
        if blocked["on"]:
            raise RuntimeError("database unavailable")

    def block_after_first_commit(session):
        if any(isinstance(o, FileRecord) for o in session.identity_map.values()):
            blocked["on"] = True

    event.listen(factory, "before_flush", fail_everything)
    event.listen(factory, "after_commit", block_after_first_commit)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert upload(client, "p.kml", points_kml(2)).status_code == 500
        blocked["on"] = False
        record = only_file(app)
        assert record.status == "PROCESSING"
        assert_envelope(client.get(f"/api/files/{record.id}/measurements/"), 409, "FILE_NOT_READY")
    event.remove(factory, "before_flush", fail_everything)
    event.remove(factory, "after_commit", block_after_first_commit)

    with TestClient(create_app(settings)) as client:
        info = strict_json(client.get(f"/api/files/{record.id}/"))
        assert (info["status"], info["error"]["code"]) == ("FAILED", "INTERRUPTED")
        assert_envelope(client.get(f"/api/files/{record.id}/measurements/"), 409, "FILE_FAILED")


class ProcessDied(BaseException):  # noqa: N818 - models the process being killed
    """Bypasses every `except Exception` handler, like the process being killed."""


def test_crash_while_storing_results_never_leaves_completed(settings, monkeypatch):
    import app.api.files as files_api

    real = files_api.apply_results
    reached = []

    def build_then_die(record, processed):
        real(record, processed)
        reached.append(True)
        raise ProcessDied

    monkeypatch.setattr(files_api, "apply_results", build_then_die)
    app = create_app(settings)
    # The request dies abruptly. How the test client reports that varies by platform and
    # timing (the exception itself, a cancellation, or a stopped portal), so only the crash
    # point and the database state afterwards are asserted.
    surfaced = None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            upload(client, "p.kml", points_kml(3))
    except BaseException as exc:  # noqa: B036 - see above
        surfaced = exc
    assert reached and surfaced is not None
    record = only_file(app)
    _, features = stored(app, record.id)
    # Nothing from the interrupted request was committed: not COMPLETED, no features.
    assert (record.status, features) == ("PROCESSING", 0)

    monkeypatch.setattr(files_api, "apply_results", real)
    with TestClient(create_app(settings)) as client:
        info = strict_json(client.get(f"/api/files/{record.id}/"))
    assert (info["status"], info["error"]["code"]) == ("FAILED", "INTERRUPTED")


def test_unexpected_measurement_error(raw_client, monkeypatch):
    def boom(*args, **kwargs):
        raise ZeroDivisionError("internal detail")

    monkeypatch.setattr("app.services.processor.measure_dataset", boom)
    error = assert_envelope(upload(raw_client, "p.kml", points_kml(1)), 500, "INTERNAL_ERROR")
    assert "internal detail" not in error["message"]
    record = only_file(raw_client.app)
    assert (record.status, record.error_code) == ("FAILED", "INTERNAL_ERROR")
    assert stored(raw_client.app, record.id)[1] == 0


# 14. Pagination ---------------------------------------------------------------------------


def test_pagination_is_stable_and_complete(client):
    file_id = strict_json(upload(client, "p.kml", points_kml(25)))["id"]
    seen, offset = [], 0
    for _ in range(10):  # bounded: a pagination bug must fail the test, not hang it
        if offset is None:
            break
        page = strict_json(
            client.get(f"/api/files/{file_id}/measurements/?limit=10&offset={offset}")
        )
        assert page["pagination"]["total"] == 25
        seen += [(f["index"], f["source_id"]) for f in page["features"]]
        offset = page["pagination"]["next_offset"]
    assert offset is None
    assert seen == [(i, f"p{i}") for i in range(25)]
    again = strict_json(client.get(f"/api/files/{file_id}/measurements/?limit=10&offset=10"))
    assert [f["index"] for f in again["features"]] == list(range(10, 20))
    beyond = strict_json(client.get(f"/api/files/{file_id}/measurements/?offset=100"))
    assert beyond["features"] == [] and beyond["pagination"]["next_offset"] is None
    default = strict_json(client.get(f"/api/files/{file_id}/measurements/"))
    assert default["pagination"]["limit"] == 100 and default["pagination"]["returned"] == 25


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("limit=0", "INVALID_PAGINATION"),
        ("limit=1001", "INVALID_PAGINATION"),
        ("offset=-1", "INVALID_PAGINATION"),
        ("limit=ten", "VALIDATION_ERROR"),
    ],
)
def test_pagination_bounds(client, query, code):
    file_id = strict_json(upload(client, "p.kml", points_kml(2)))["id"]
    assert_envelope(client.get(f"/api/files/{file_id}/measurements/?{query}"), 422, code)


def test_maximum_page_size_is_configurable(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", max_page_size=5)
    with TestClient(create_app(settings)) as client:
        file_id = strict_json(upload(client, "p.kml", points_kml(8)))["id"]
        assert (
            strict_json(client.get(f"/api/files/{file_id}/measurements/?limit=5"))["pagination"][
                "returned"
            ]
            == 5
        )
        assert_envelope(
            client.get(f"/api/files/{file_id}/measurements/?limit=6"), 422, "INVALID_PAGINATION"
        )


# 15. Every required error status, with the standard envelope -----------------------------


def test_error_statuses_and_envelopes(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", max_features=3, max_upload_bytes=50_000)
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert_envelope(client.get("/api/files/not-an-id/"), 404, "FILE_NOT_FOUND")
        assert_envelope(
            client.get("/api/files/00000000-0000-0000-0000-000000000000/measurements/"),
            404,
            "FILE_NOT_FOUND",
        )
        # 409 while processing (a row that is mid-request).
        with app.state.session_factory() as session:
            session.add(
                FileRecord(
                    id="22222222-2222-2222-2222-222222222222",
                    original_filename="x.kml",
                    format="KML",
                    storage_path="x",
                    size_bytes=1,
                    sha256="0" * 64,
                    status="PROCESSING",
                    warnings=[],
                )
            )
            session.commit()
        assert_envelope(
            client.get("/api/files/22222222-2222-2222-2222-222222222222/measurements/"),
            409,
            "FILE_NOT_READY",
        )
        # 413: upload size and processing limits.
        assert_envelope(upload(client, "big.kml", b"x" * 60_000), 413, "UPLOAD_TOO_LARGE")
        too_many = assert_envelope(upload(client, "p.kml", points_kml(4)), 413, "TOO_MANY_FEATURES")
        assert strict_json(client.get(f"/api/files/{too_many['file_id']}/"))["status"] == "FAILED"
        # 415.
        assert_envelope(upload(client, "a.geojson", b"{}"), 415, "UNSUPPORTED_FORMAT")
        assert_envelope(upload(client, "a.kmz", b"PK"), 415, "KMZ_UNSUPPORTED")
        # 422: empty, broken dataset, invalid source_crs, CRS conflict.
        assert_envelope(upload(client, "e.kml", b""), 422, "EMPTY_FILE")
        assert_envelope(upload(client, "b.kml", b"<kml><Document>"), 422, "INVALID_KML")
        assert_envelope(
            upload(client, "p.kml", points_kml(1), "not a crs"), 422, "INVALID_SOURCE_CRS"
        )
        conflict = assert_envelope(
            upload(client, "p.kml", points_kml(1), "EPSG:32643"), 422, "CRS_CONFLICT"
        )
        assert_envelope(
            client.get(f"/api/files/{conflict['file_id']}/measurements/"), 409, "FILE_FAILED"
        )
        assert_envelope(client.post("/api/files/", data={"x": "1"}), 422, "VALIDATION_ERROR")


def test_invalid_source_crs_is_rejected_before_anything_is_stored(client, settings):
    assert_envelope(upload(client, "p.kml", points_kml(1), "EPSG:0"), 422, "INVALID_SOURCE_CRS")
    assert list(settings.upload_dir.iterdir()) == []
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(FileRecord)) == 0


# 16. Corrupted uploads never leave partial results ---------------------------------------


@pytest.mark.parametrize("damage", ["truncate_shp", "truncate_dbf", "corrupt_zip_entry"])
def test_corrupted_shapefile_upload_leaves_no_partial_dataset(client, tmp_path, damage):
    ring = square(500_000, 1_434_000, 1000)
    records = [
        {
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"plot_id": str(i), "owner": "x"},
        }
        for i in range(5)
    ]
    files = write_shapefile(
        tmp_path / "s",
        name="parcels",
        schema={"geometry": "Polygon", "properties": {"plot_id": "str", "owner": "str"}},
        records=records,
        crs="EPSG:32643",
    )
    if damage == "truncate_shp":
        files["parcels.shp"] = files["parcels.shp"][:-30]
    elif damage == "truncate_dbf":
        files["parcels.dbf"] = files["parcels.dbf"][:-20]
    archive = bytearray(make_zip(files.items()))
    if damage == "corrupt_zip_entry":
        # Flip a byte inside the .shp entry's compressed data (located from its header), so
        # the CRC check fails while every header stays intact.
        with zipfile.ZipFile(io.BytesIO(bytes(archive))) as zf:
            info = zf.getinfo("parcels.shp")
        start = info.header_offset + 30 + len(info.filename) + len(info.extra)
        archive[start + 5] ^= 0xFF
    response = upload(client, "parcels.zip", bytes(archive))
    error = assert_envelope(response, 422, response.json()["error"]["code"])
    assert error["code"] in {"MALFORMED_DATASET", "INVALID_ZIP"}
    record, features = stored(client.app, error["file_id"])
    assert (record.status, features, record.status_counts) == ("FAILED", 0, None)
    assert_envelope(client.get(f"/api/files/{error['file_id']}/measurements/"), 409, "FILE_FAILED")


def test_valid_upload_after_failures_is_unaffected(client, tmp_path):
    upload(client, "bad.zip", b"PK\x03\x04 broken")
    info = strict_json(
        upload(
            client,
            "ok.zip",
            polygon_zip(
                tmp_path,
                [(square(500_000, 1_434_000, 1000), {"plot_id": "1", "owner": "x"})],
                "EPSG:32643",
            ),
        )
    )
    assert (info["status"], info["counts"]) == ("COMPLETED", {"MEASURED": 1})


# Numerical integrity through the API ------------------------------------------------------


def two_polygons_kml() -> bytes:
    ring = "77,13 77.01,13 77.01,13.01 77,13.01 77,13"
    shifted = "77.02,13 77.03,13 77.03,13.01 77.02,13.01 77.02,13"
    marks = "".join(
        f'<Placemark id="{name}"><Polygon><outerBoundaryIs><LinearRing><coordinates>{c}'
        "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        for name, c in (("a", ring), ("b", shifted))
    )
    return f"{KML_HEAD}{marks}</Document></kml>".encode()


def persisted_status_counts(app, file_id: str) -> dict[str, int]:
    with app.state.session_factory() as session:
        rows = session.execute(
            select(FeatureRecord.status, func.count())
            .where(FeatureRecord.file_id == file_id)
            .group_by(FeatureRecord.status)
        ).all()
    return dict(rows)


def test_injected_non_finite_measurement_is_an_explicit_feature_failure(client, monkeypatch):
    from app.services import measurements

    real = measurements._geodesic_area
    calls = []

    def nan_first(geometry):
        calls.append(1)
        return float("nan") if len(calls) == 1 else real(geometry)

    monkeypatch.setattr(measurements, "_geodesic_area", nan_first)
    response = upload(client, "two.kml", two_polygons_kml())
    assert response.status_code == 201
    info = strict_json(response)
    assert info["status"] == "COMPLETED"
    assert info["counts"] == {"MEASURED": 1, "TRANSFORM_FAILED": 1}
    assert persisted_status_counts(client.app, info["id"]) == info["counts"]
    failed, ok = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"]
    assert (failed["status"], failed["reason_code"]) == (
        "TRANSFORM_FAILED",
        "NON_FINITE_MEASUREMENT",
    )
    assert (failed["area_m2"], failed["geodesic_reference"]) == (None, None)
    assert ok["status"] == "MEASURED" and ok["area_m2"] > 0


def test_inconsistent_result_is_never_stored(raw_client, monkeypatch):
    from app.services import processor

    real = processor.measure_dataset

    def corrupt(*args, **kwargs):
        measured = real(*args, **kwargs)
        measured.features[0].area_m2 = None  # MEASURED without its metric
        return measured

    monkeypatch.setattr(processor, "measure_dataset", corrupt)
    error = assert_envelope(
        upload(raw_client, "two.kml", two_polygons_kml()), 500, "INTERNAL_ERROR"
    )
    assert "request id" in error["message"]
    record = only_file(raw_client.app)
    _, features = stored(raw_client.app, record.id)
    assert (record.status, record.error_code, features) == ("FAILED", "INCONSISTENT_RESULT", 0)
    assert record.status_counts is None
    assert_envelope(raw_client.get(f"/api/files/{record.id}/measurements/"), 409, "FILE_FAILED")


def test_counts_always_match_persisted_statuses(client):
    kml = (
        f"{KML_HEAD}<Placemark><Point><coordinates>77,13</coordinates></Point></Placemark>"
        "<Placemark><LineString><coordinates>77,13 77.01,13</coordinates></LineString></Placemark>"
        "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 1,1 1,0 0,1 0,0"
        "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        "<Placemark><LineString><coordinates>179,0 -179,0</coordinates></LineString></Placemark>"
        "</Document></kml>"
    ).encode()
    info = strict_json(upload(client, "mix.kml", kml))
    assert info["counts"] == {
        "INVALID_GEOMETRY": 1,
        "MEASURED": 1,
        "NOT_APPLICABLE": 1,
        "UNSUPPORTED_EXTENT": 1,
    }
    assert persisted_status_counts(client.app, info["id"]) == info["counts"]
