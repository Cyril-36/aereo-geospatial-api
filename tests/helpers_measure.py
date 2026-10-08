"""Small builders shared by the measurement regression tests."""

from pyproj import CRS

from app.services import measurements as m
from app.services.dataset import Dataset, RawFeature, SourceCrs


def source(code: str) -> SourceCrs:
    return SourceCrs("KNOWN", code, CRS.from_user_input(code).to_wkt(), "PRJ_FILE")


def feature(geometry, index=0, **kwargs) -> RawFeature:
    kind = geometry.get("type") if isinstance(geometry, dict) else None
    return RawFeature(index, None, kind, geometry, {}, **kwargs)


def line(coords) -> dict:
    return {"type": "LineString", "coordinates": [list(c) for c in coords]}


def run(features, crs_source, limits=None):
    return m.measure_dataset(Dataset("SHAPEFILE", crs_source, features), limits).features
