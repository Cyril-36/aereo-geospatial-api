"""Regressions: Database failures: no attribute values in logs, and the file is marked FAILED."""

import logging
import sqlite3

from fastapi.testclient import TestClient

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


# D. A database error logged SQL parameters, i.e. attribute values.
def test_database_errors_never_log_attribute_values(settings, caplog):
    marker = "SENSITIVE-OWNER-7731"
    data = kml(
        "<Placemark><name>x</name><ExtendedData><Data name='owner'>"
        f"<value>{marker}</value></Data></ExtendedData>{polygon(SQUARE)}</Placemark>"
    )
    with TestClient(create_app(settings)):
        pass  # create and migrate the database
    connection = sqlite3.connect(settings.data_dir / "aereo.db")
    connection.execute(
        "CREATE TRIGGER block BEFORE INSERT ON features "
        "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END"
    )
    connection.commit()
    connection.close()
    with (
        caplog.at_level(logging.DEBUG),
        TestClient(create_app(settings), raise_server_exceptions=False) as client,
    ):
        response = upload(client, "f.kml", data)
    assert response.status_code == 500
    assert marker not in response.text
    logged = "\n".join(
        r.getMessage() + (logging.Formatter().formatException(r.exc_info) if r.exc_info else "")
        for r in caplog.records
    )
    assert "simulated disk failure" in logged  # the failure itself is still logged
    assert marker not in logged


# F. After a real database error during flush, recording the failure itself raised
# (reading an expired attribute on a session awaiting rollback), leaving the file PROCESSING.
def test_real_database_error_marks_the_file_failed(settings):
    from sqlalchemy import func, select

    from app.models import FeatureRecord, FileRecord

    with TestClient(create_app(settings)):
        pass
    connection = sqlite3.connect(settings.data_dir / "aereo.db")
    connection.execute(
        "CREATE TRIGGER block BEFORE INSERT ON features "
        "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END"
    )
    connection.commit()
    connection.close()
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = upload(client, "f.kml", kml(f"<Placemark>{polygon(SQUARE)}</Placemark>"))
        assert response.status_code == 500
        with app.state.session_factory() as session:
            record = session.scalars(select(FileRecord)).one()
            features = session.scalar(select(func.count()).select_from(FeatureRecord))
        assert (record.status, record.error_code, features) == ("FAILED", "PERSISTENCE_FAILED", 0)
        error = strict_json(client.get(f"/api/files/{record.id}/measurements/"))["error"]
        assert error["code"] == "FILE_FAILED"
