"""Regressions for findings from an external audit of the merged main branch."""

import io
import struct
import zipfile
from pathlib import Path

import pytest
from pyproj import CRS

from app.errors import IngestionError
from app.services import crs as crs_service
from app.services.crs import kml_crs
from app.services.dataset import Dataset, IngestionLimits, RawFeature, SourceCrs
from app.services.measurements import Status, measure_dataset
from app.services.safe_zip import extract_shapefile
from tests.e2e_helpers import strict_json, upload

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# A ~65 km polygon whose tiny hole sits just above the southern edge. In lon/lat the hole is
# inside the shell; along the WGS84 geodesic the southern edge bows north past the hole.
SHELL = [[77, 13], [77.6, 13], [77.6, 13.2], [77, 13.2], [77, 13]]
HOLE = [
    [77.29, 13.00001],
    [77.31, 13.00001],
    [77.31, 13.00003],
    [77.29, 13.00003],
    [77.29, 13.00001],
]


def nad27(*points) -> Dataset:
    source = SourceCrs("KNOWN", "EPSG:4267", CRS.from_epsg(4267).to_wkt(), "PRJ_FILE")
    features = [
        RawFeature(i, None, "Point", {"type": "Point", "coordinates": list(p)}, {})
        for i, p in enumerate(points)
    ]
    return Dataset("SHAPEFILE", source, features)


# 1. Validity must hold for the geometry that is actually measured.
def test_polygon_invalid_under_geodesic_edges_is_not_measured():
    feature = RawFeature(0, None, "Polygon", {"type": "Polygon", "coordinates": [SHELL, HOLE]}, {})
    result = measure_dataset(Dataset("KML", kml_crs(), [feature])).features[0]
    assert (result.status, result.reason_code) == (
        Status.INVALID_GEOMETRY,
        "INVALID_AFTER_DENSIFICATION",
    )
    assert "Hole lies outside shell" in result.reason
    assert (result.area_m2, result.geodesic_area_m2, result.generated_vertices) == (None, None, 0)


def test_same_polygon_without_the_hole_is_measured():
    feature = RawFeature(0, None, "Polygon", {"type": "Polygon", "coordinates": [SHELL]}, {})
    assert measure_dataset(Dataset("KML", kml_crs(), [feature])).features[0].status == "MEASURED"


def test_invalid_after_densification_over_http(client):
    ring = lambda r: " ".join(f"{x},{y}" for x, y in r)  # noqa: E731
    kml = (
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><Polygon>'
        f"<outerBoundaryIs><LinearRing><coordinates>{ring(SHELL)}</coordinates></LinearRing>"
        f"</outerBoundaryIs><innerBoundaryIs><LinearRing><coordinates>{ring(HOLE)}</coordinates>"
        "</LinearRing></innerBoundaryIs></Polygon></Placemark></kml>"
    ).encode()
    info = strict_json(upload(client, "hole.kml", kml))
    assert info["counts"] == {"INVALID_GEOMETRY": 1}
    feature = strict_json(client.get(f"/api/files/{info['id']}/measurements/"))["features"][0]
    assert feature["area_m2"] is None and feature["reason_code"] == "INVALID_AFTER_DENSIFICATION"


# 2. Corrupt compressed data is invalid input, not a server error.
def corrupt_deflate_zip() -> bytes:
    data = bytearray((SAMPLES / "sample_parcels.zip").read_bytes())
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        member = next(i for i in zf.infolist() if i.filename.endswith(".shp"))
    name_len, extra_len = struct.unpack_from("<HH", data, member.header_offset + 26)
    start = member.header_offset + 30 + name_len + extra_len
    data[start] = (data[start] & 0xF8) | 0x07  # invalid DEFLATE block type
    return bytes(data)


def test_corrupt_deflate_stream_is_invalid_zip(tmp_path):
    archive = tmp_path / "c.zip"
    archive.write_bytes(corrupt_deflate_zip())
    with pytest.raises(IngestionError) as exc_info:
        extract_shapefile(archive, tmp_path / "out", IngestionLimits(10**8, 100, 100, 10**6))
    assert exc_info.value.code == "INVALID_ZIP"


def test_corrupt_deflate_stream_over_http_is_422(client):
    response = upload(client, "c.zip", corrupt_deflate_zip())
    assert response.status_code == 422, response.text
    error = strict_json(response)["error"]
    assert error["code"] == "INVALID_ZIP"
    assert strict_json(client.get(f"/api/files/{error['file_id']}/"))["status"] == "FAILED"


# 3. The datum transformation must be valid where the data is.
def area_of_use(result):
    return result.crs.to_wgs84.area_of_use


def contains(area, lon, lat) -> bool:
    return area.west <= lon <= area.east and area.south <= lat <= area.north


@pytest.mark.parametrize("point", [(-118.25, 34.05), (-100.0, 40.0), (-114.0, 51.0)])
def test_datum_operation_covers_the_data(point):
    result = measure_dataset(nad27(point))
    assert contains(area_of_use(result), *point), area_of_use(result)
    assert "TRANSFORMATION_OUTSIDE_AREA_OF_USE" not in [w["code"] for w in result.warnings]


def test_global_choice_would_not_cover_california():
    # Premise of the finding: without the data's location, PROJ's first choice is Canadian.
    unlocated = crs_service.resolve(nad27().source_crs)
    assert not contains(unlocated.to_wgs84.area_of_use, -118.25, 34.05)


def test_data_outside_every_regional_operation_falls_back_with_a_warning():
    # NAD27 coordinates in India: no regional NAD27 operation applies, so PROJ falls back to a
    # ballpark operation (worldwide, datum shift omitted), which is flagged as such.
    result = measure_dataset(nad27((77.6, 13.0)))
    assert "Ballpark" in result.crs.transformation.operation
    assert [w["code"] for w in result.warnings] == ["TRANSFORMATION_ACCURACY_UNKNOWN"]


def test_data_spanning_regions_is_warned_when_the_operation_does_not_cover_it():
    # California and Alberta: no single regional operation covers both.
    result = measure_dataset(nad27((-118.25, 34.05), (-114.0, 51.0)))
    assert "TRANSFORMATION_OUTSIDE_AREA_OF_USE" in [w["code"] for w in result.warnings]


def test_projected_source_uses_its_inverse_projection_for_the_location():
    # NAD27 / UTM 11N coordinates in Los Angeles must select a US operation, located through
    # the CRS's own inverse projection (not by treating metres as degrees).
    from pyproj import Transformer

    x, y = Transformer.from_crs(4267, 26711, always_xy=True).transform(-118.25, 34.05)
    source = SourceCrs("KNOWN", "EPSG:26711", CRS.from_epsg(26711).to_wkt(), "PRJ_FILE")
    feature = RawFeature(0, None, "Point", {"type": "Point", "coordinates": [x, y]}, {})
    result = measure_dataset(Dataset("SHAPEFILE", source, [feature]))
    assert contains(area_of_use(result), -118.25, 34.05), area_of_use(result)
