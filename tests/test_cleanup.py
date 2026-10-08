"""Cleanup respects live uploads, references and restart recovery."""

import os
import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cleanup import sweep_orphan_uploads


def test_restart_removes_only_aged_orphans(settings):
    with TestClient(create_app(settings)):
        old = settings.upload_dir / "orphan.kml"
        old.write_bytes(b"old")
        os.utime(old, (time.time() - 3600,) * 2)
        fresh = settings.upload_dir / "active.kml.part"
        fresh.write_bytes(b"pending")
    with TestClient(create_app(settings)):
        assert not old.exists()
        assert fresh.exists()


def test_sweep_skips_symlinks_and_directories(client, settings, tmp_path):
    target = tmp_path / "keep.kml"
    target.write_bytes(b"keep")
    link = settings.upload_dir / "link.kml"
    link.symlink_to(target)
    (settings.upload_dir / "nested").mkdir()
    assert sweep_orphan_uploads(client.app.state.session_factory, settings.upload_dir, 0) == []
    assert target.read_bytes() == b"keep"


def test_sweep_unlink_failure_is_recoverable(client, settings, monkeypatch):
    from pathlib import Path

    old = settings.upload_dir / "orphan.kml"
    old.write_bytes(b"old")
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path == old:
            raise OSError("unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail)
    assert sweep_orphan_uploads(client.app.state.session_factory, settings.upload_dir, 0) == []
    assert old.exists()
