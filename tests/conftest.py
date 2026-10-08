"""Fixtures are generated in code: real Shapefiles written by Fiona, KML written as text."""

import io
import zipfile
from collections.abc import Iterable
from pathlib import Path

import fiona
import pytest

from app.config import Settings
from app.services.dataset import IngestionLimits

LIMITS = IngestionLimits(
    max_expanded_bytes=10 * 1024 * 1024,
    max_archive_members=50,
    max_features=1_000,
    max_vertices=100_000,
)

PLOTS_SCHEMA = {
    "geometry": "Polygon",
    "properties": {"name": "str", "owner": "str", "survey_no": "int", "area_ha": "float"},
}
PLOT_A = [(77.59, 12.97), (77.60, 12.97), (77.60, 12.98), (77.59, 12.98), (77.59, 12.97)]
PLOT_B = [(77.61, 12.97), (77.62, 12.97), (77.62, 12.98), (77.61, 12.97)]
PLOT_RECORDS = [
    {
        "geometry": {"type": "Polygon", "coordinates": [PLOT_A]},
        "properties": {"name": "Plot A", "owner": "Rao", "survey_no": 17, "area_ha": 1.5},
    },
    {
        "geometry": {"type": "Polygon", "coordinates": [PLOT_B]},
        "properties": {"name": "Plot B", "owner": "Iyer", "survey_no": 18, "area_ha": None},
    },
]


def write_shapefile(
    directory: Path,
    name: str = "plots",
    schema: dict | None = None,
    records: Iterable[dict] = PLOT_RECORDS,
    crs: str | None = "EPSG:4326",
    encoding: str | None = None,
) -> dict[str, bytes]:
    """Write a real Shapefile with Fiona and return {component filename: bytes}."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.shp"
    kwargs = {"driver": "ESRI Shapefile", "schema": schema or PLOTS_SCHEMA}
    if crs is not None:
        kwargs["crs"] = crs
    if encoding is not None:
        kwargs["encoding"] = encoding
    with fiona.open(path, "w", **kwargs) as dst:
        for record in records:
            dst.write(fiona.Feature.from_dict(record))
    return {p.name: p.read_bytes() for p in sorted(directory.glob(f"{name}.*"))}


def make_zip(entries: Iterable[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


def shapefile_zip(tmp_path: Path, prefix: str = "", **kwargs) -> bytes:
    files = write_shapefile(tmp_path / "src", **kwargs)
    return make_zip((prefix + name, data) for name, data in files.items())


def kml(body: str, namespace: str | None = "http://www.opengis.net/kml/2.2") -> bytes:
    ns = f' xmlns="{namespace}"' if namespace else ""
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<kml{ns}>{body}</kml>'.encode()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client
