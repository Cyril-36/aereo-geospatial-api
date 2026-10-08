"""The workspace shell, its static files and the UI configuration endpoint."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

WEB_PATHS = ["/", "/history", "/files/3f2a0c5e-0000-4000-8000-000000000000", "/files/not-an-id"]


def test_config_reports_configured_limits(client, settings):
    assert client.get("/api/config/").json() == {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_features": settings.max_features,
        "default_page_size": settings.default_page_size,
        "max_page_size": settings.max_page_size,
        "accepted_extensions": [".zip", ".kml"],
        "map_tile_url": settings.map_tile_url,
        "map_max_features": 5000,
        "map_max_vertices": 250000,
    }


def test_config_follows_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AEREO_MAP_TILE_URL", "")
    monkeypatch.setenv("AEREO_MAX_UPLOAD_BYTES", "1234")
    monkeypatch.setenv("AEREO_MAP_MAX_FEATURES", "100")
    with TestClient(create_app(Settings(data_dir=tmp_path))) as c:
        body = c.get("/api/config/").json()
    assert (body["map_tile_url"], body["max_upload_bytes"], body["map_max_features"]) == (
        "",
        1234,
        100,
    )


@pytest.mark.parametrize("path", WEB_PATHS)
def test_app_routes_serve_the_shell(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    assert '<main id="app"' in response.text


def test_static_assets_are_served(client):
    response = client.get("/static/js/main.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


@pytest.mark.parametrize("path", ["/api/unknown", "/files/x/y", "/static/nope.js", "/historyx"])
def test_unknown_paths_still_404_with_envelope(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
