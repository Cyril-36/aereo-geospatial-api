"""Builders for end-to-end tests: real zipped Shapefiles (written by Fiona) and KML text."""

import json
import math

from fastapi.testclient import TestClient
from pyproj import Geod, Proj, Transformer

from tests.conftest import make_zip, write_shapefile

GEOD = Geod(ellps="WGS84")
US_FOOT = 1200 / 3937


def strict_json(response) -> dict:
    """Parse a response, failing on NaN/Infinity, which strict JSON does not allow."""

    def reject(token):
        raise AssertionError(f"non-standard JSON constant {token}")

    return json.loads(response.text, parse_constant=reject)


def upload(client: TestClient, name: str, data: bytes, source_crs: str | None = None):
    form = {"source_crs": source_crs} if source_crs is not None else None
    return client.post("/api/files/", files={"file": (name, data)}, data=form)


def square(x0: float, y0: float, side: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side), (x0, y0)]


def polygon_zip(tmp_path, rings_and_props, crs: str | None, name: str = "parcels") -> bytes:
    schema = {"geometry": "Polygon", "properties": {"plot_id": "str", "owner": "str"}}
    records = [
        {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props}
        for ring, props in rings_and_props
    ]
    files = write_shapefile(tmp_path / name, name=name, schema=schema, records=records, crs=crs)
    return make_zip(files.items())


def projected_ground_area(code: str, ring, side_m: float) -> float:
    """Ground area of a small grid square: grid area / PROJ's analytic areal scale."""
    cx = sum(x for x, _ in ring[:-1]) / 4
    cy = sum(y for _, y in ring[:-1]) / 4
    lon, lat = Transformer.from_crs(code, 4326, always_xy=True).transform(cx, cy)
    return side_m * side_m / Proj(code).get_factors(lon, lat).areal_scale


def geodesic_square(lon: float, lat: float, side_m: float) -> list[tuple[float, float]]:
    e = GEOD.fwd(lon, lat, 90, side_m)
    n = GEOD.fwd(lon, lat, 0, side_m)
    ne = GEOD.fwd(e[0], e[1], 0, side_m)
    return [(lon, lat), (e[0], e[1]), (ne[0], ne[1]), (n[0], n[1]), (lon, lat)]


def geodesic_area(ring) -> float:
    lons, lats = zip(*ring, strict=True)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def kml_coords(ring) -> str:
    return " ".join(f"{x!r},{y!r}" for x, y in ring)


def is_close(a: float, b: float, rel: float) -> bool:
    return math.isclose(a, b, rel_tol=rel)
