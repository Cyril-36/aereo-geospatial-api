import os
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def up(client, name, crs=None):
    r = client.post(
        "/api/files/",
        files={"file": (name, (SAMPLES / name).read_bytes())},
        data={"source_crs": crs} if crs else None,
    )
    return r.json()["id"] if r.status_code == 201 else r.json()["error"]["file_id"]


def test_history_lists_newest_first_with_failed(client):
    a = up(client, "sample_parcels.zip")
    b = up(client, "sample_parcels.zip", "EPSG:4326")  # CRS_CONFLICT, stored as FAILED
    c = up(client, "sample_survey.kml")
    body = client.get("/api/files/").json()
    assert [f["id"] for f in body["files"]] == [c, b, a]
    assert body["files"][1]["status"] == "FAILED"
    assert body["pagination"] == {
        "limit": 50,
        "offset": 0,
        "total": 3,
        "returned": 3,
        "next_offset": None,
    }


def test_history_filters_and_unicode_search(client):
    up(client, "sample_survey.kml")
    kid = client.post(
        "/api/files/",
        files={"file": ("ಬೆಂಗಳೂರು_Survey.kml", (SAMPLES / "sample_survey.kml").read_bytes())},
    ).json()["id"]
    assert [f["id"] for f in client.get("/api/files/?q=ಬೆಂಗಳೂರು_survey").json()["files"]] == [kid]
    assert client.get(f"/api/files/?q={kid[:8].upper()}").json()["pagination"]["total"] == 1
    assert client.get("/api/files/?format=SHAPEFILE").json()["pagination"]["total"] == 0
    assert client.get("/api/files/?status=COMPLETED").json()["pagination"]["total"] == 2


@pytest.mark.parametrize(
    "qs", ["status=DONE", "format=GEOJSON", "limit=0", "limit=201", "offset=-1", "q=" + "x" * 201]
)
def test_history_rejects_bad_params(client, qs):
    r = client.get(f"/api/files/?{qs}")
    assert r.status_code == 422
    assert r.json()["error"]["code"] in {"INVALID_QUERY", "INVALID_PAGINATION"}


def test_delete_removes_record_features_and_upload(client, settings):
    fid = up(client, "sample_parcels.zip")
    stored = next(settings.upload_dir.glob(f"{fid}.*"))
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    assert not stored.exists()
    assert client.get(f"/api/files/{fid}/").status_code == 404
    with client.app.state.session_factory() as s:
        from sqlalchemy import func, select

        from app.models import FeatureRecord

        assert s.scalar(select(func.count()).select_from(FeatureRecord)) == 0
    assert client.delete(f"/api/files/{fid}/").status_code == 404


def test_delete_while_processing_is_refused(client):
    fid = up(client, "sample_survey.kml")
    with client.app.state.session_factory() as s:
        from app.models import FileRecord

        s.get(FileRecord, fid).status = "PROCESSING"
        s.commit()
    r = client.delete(f"/api/files/{fid}/")
    assert r.status_code == 409 and r.json()["error"]["code"] == "FILE_NOT_READY"


def test_unlink_failure_still_deletes_and_restart_cleans(client, settings, monkeypatch):
    fid = up(client, "sample_parcels.zip")
    stored = next(settings.upload_dir.glob(f"{fid}.*"))
    real_unlink = Path.unlink

    def boom(self, *a, **k):
        if self == stored:
            raise OSError("disk says no")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom)
    assert client.delete(f"/api/files/{fid}/").status_code == 204
    assert stored.exists()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    old = stored.stat().st_mtime - 3600
    os.utime(stored, (old, old))
    from app.services.cleanup import sweep_orphan_uploads

    removed = sweep_orphan_uploads(client.app.state.session_factory, settings.upload_dir)
    assert removed == [stored] and not stored.exists()


def test_sweep_keeps_referenced_and_fresh_files(client, settings):
    fid = up(client, "sample_survey.kml")
    fresh = settings.upload_dir / "in-flight.kml"
    fresh.write_bytes(b"x")
    from app.services.cleanup import sweep_orphan_uploads

    assert sweep_orphan_uploads(client.app.state.session_factory, settings.upload_dir) == []
    assert fresh.exists() and next(settings.upload_dir.glob(f"{fid}.*")).exists()


@pytest.mark.parametrize("name", ["q", "status", "format", "limit", "offset", "since"])
def test_history_rejects_duplicate_parameters(client, name):
    response = client.get("/api/files/", params=[(name, "x"), (name, "y")])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DUPLICATE_QUERY_PARAMETER"


def test_history_since_and_pagination(client):
    from datetime import UTC, datetime

    from app.models import FileRecord

    first = up(client, "sample_survey.kml")
    second = up(client, "sample_parcels.zip")
    with client.app.state.session_factory() as session:
        session.get(FileRecord, first).created_at = datetime(2020, 1, 1, tzinfo=UTC)
        session.commit()
    body = client.get("/api/files/", params={"since": "2021-01-01T00:00:00Z"}).json()
    assert [f["id"] for f in body["files"]] == [second]
    first_page = client.get("/api/files/?limit=1").json()
    second_page = client.get("/api/files/?limit=1&offset=1").json()
    assert first_page["pagination"]["next_offset"] == 1
    assert second_page["files"][0]["id"] == first
    assert client.get("/api/files/?offset=999999999999999999999").json()["files"] == []


@pytest.mark.parametrize("since", ["bad", "2026-10-08", "2026-10-08T12:00:00"])
def test_history_requires_timezone_for_since(client, since):
    response = client.get("/api/files/", params={"since": since})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUERY"
