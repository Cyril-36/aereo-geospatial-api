from pathlib import Path

import pytest

from app.errors import IngestionError
from app.services.dataset import IngestionLimits
from app.services.processor import extract
from tests.conftest import LIMITS, PLOT_A, make_zip, write_shapefile

UTM_SQUARE = [(500000, 1434000), (501000, 1434000), (501000, 1435000), (500000, 1435000)]


def read_zip(tmp_path: Path, archive: bytes, limits: IngestionLimits = LIMITS):
    upload = tmp_path / "upload.zip"
    upload.write_bytes(archive)
    return extract(upload, "SHAPEFILE", limits, tmp_path / "work")


def zip_of(files: dict[str, bytes], drop: str | None = None) -> bytes:
    return make_zip((n, d) for n, d in files.items() if not (drop and n.endswith(drop)))


def test_reads_geometry_attributes_and_declared_crs(tmp_path):
    dataset = read_zip(tmp_path, zip_of(write_shapefile(tmp_path / "s")))

    assert dataset.format == "SHAPEFILE"
    assert dataset.source_crs.status == "KNOWN"
    assert dataset.source_crs.identifier == "EPSG:4326"
    assert dataset.source_crs.origin == "PRJ_FILE"
    assert dataset.warnings == []
    assert [f.index for f in dataset.features] == [0, 1]
    assert [f.source_id for f in dataset.features] == ["0", "1"]

    first = dataset.features[0]
    assert first.geometry_type == "Polygon"
    assert first.geometry["type"] == "Polygon"
    # The Shapefile format stores outer rings clockwise, so GDAL wrote this counter-clockwise
    # fixture reversed; the reader returns exactly what the file contains.
    assert [tuple(p) for p in first.geometry["coordinates"][0]] == PLOT_A[::-1]
    assert first.properties == {"name": "Plot A", "owner": "Rao", "survey_no": 17, "area_ha": 1.5}
    assert dataset.features[1].properties["area_ha"] is None  # null attribute stays null


def test_projected_source_coordinates_are_preserved_untouched(tmp_path):
    records = [
        {
            "geometry": {"type": "Polygon", "coordinates": [UTM_SQUARE + UTM_SQUARE[:1]]},
            "properties": {"name": "grid", "owner": "x", "survey_no": 1, "area_ha": 100.0},
        }
    ]
    files = write_shapefile(tmp_path / "s", records=records, crs="EPSG:32643")
    dataset = read_zip(tmp_path, zip_of(files))
    assert dataset.source_crs.identifier == "EPSG:32643"
    ring = dataset.features[0].geometry["coordinates"][0]
    assert {tuple(p) for p in ring} == set(UTM_SQUARE)


def test_missing_prj_is_unknown_never_inferred(tmp_path):
    # Coordinates look like lon/lat, but without a .prj the CRS must stay UNKNOWN.
    dataset = read_zip(tmp_path, zip_of(write_shapefile(tmp_path / "s"), drop=".prj"))
    assert dataset.source_crs.status == "UNKNOWN"
    assert dataset.source_crs.identifier is None
    assert dataset.source_crs.origin == "NONE"
    assert [w["code"] for w in dataset.warnings] == ["CRS_MISSING"]
    assert len(dataset.features) == 2  # features are kept


def test_unparseable_prj_is_invalid_not_guessed(tmp_path):
    files = write_shapefile(tmp_path / "s")
    files["plots.prj"] = b"this is not WKT"
    dataset = read_zip(tmp_path, zip_of(files))
    assert dataset.source_crs.status == "INVALID"
    assert dataset.source_crs.identifier is None
    assert [w["code"] for w in dataset.warnings] == ["CRS_INVALID"]


def test_cpg_encoding_is_honoured(tmp_path):
    records = [
        {
            "geometry": {"type": "Polygon", "coordinates": [PLOT_A]},
            "properties": {"name": "Zürich Süd", "owner": "Müller", "survey_no": 1, "area_ha": 2.0},
        }
    ]
    files = write_shapefile(tmp_path / "s", records=records, encoding="cp1252")
    assert files["plots.cpg"].strip().upper() in {b"1252", b"CP1252", b"WINDOWS-1252"}
    assert "Zürich".encode("cp1252") in files["plots.dbf"]  # really stored as cp1252
    dataset = read_zip(tmp_path, zip_of(files))
    assert dataset.features[0].properties["name"] == "Zürich Süd"
    assert dataset.features[0].properties["owner"] == "Müller"


def test_null_geometry_is_kept_as_a_feature(tmp_path):
    records = [
        {
            "geometry": None,
            "properties": {"name": "no shape", "owner": "x", "survey_no": 1, "area_ha": 0.0},
        },
        {
            "geometry": {"type": "Polygon", "coordinates": [PLOT_A]},
            "properties": {"name": "ok", "owner": "y", "survey_no": 2, "area_ha": 1.0},
        },
    ]
    dataset = read_zip(tmp_path, zip_of(write_shapefile(tmp_path / "s", records=records)))
    assert len(dataset.features) == 2
    assert dataset.features[0].geometry is None
    assert dataset.features[0].geometry_type is None
    assert dataset.features[0].properties["name"] == "no shape"
    assert dataset.features[1].geometry_type == "Polygon"


def test_line_and_point_layers(tmp_path):
    schema = {"geometry": "LineString", "properties": {"road": "str"}}
    records = [
        {
            "geometry": {"type": "LineString", "coordinates": [(77.5, 12.9), (77.6, 13.0)]},
            "properties": {"road": "NH44"},
        }
    ]
    files = write_shapefile(tmp_path / "s", name="roads", schema=schema, records=records)
    dataset = read_zip(tmp_path, zip_of(files))
    assert dataset.features[0].geometry_type == "LineString"
    assert dataset.features[0].properties == {"road": "NH44"}


def test_feature_limit_fails_instead_of_truncating(tmp_path):
    limits = IngestionLimits(10**8, 50, 1, 100_000)
    with pytest.raises(IngestionError) as exc_info:
        read_zip(tmp_path, zip_of(write_shapefile(tmp_path / "s")), limits)
    assert exc_info.value.code == "TOO_MANY_FEATURES"


def test_vertex_limit_fails_instead_of_truncating(tmp_path):
    limits = IngestionLimits(10**8, 50, 100, 6)  # the two plots have 5 + 4 vertices
    with pytest.raises(IngestionError) as exc_info:
        read_zip(tmp_path, zip_of(write_shapefile(tmp_path / "s")), limits)
    assert exc_info.value.code == "TOO_MANY_VERTICES"


def test_corrupt_shp_is_malformed_dataset(tmp_path):
    files = write_shapefile(tmp_path / "s")
    files["plots.shp"] = b"\x00" * 20
    with pytest.raises(IngestionError) as exc_info:
        read_zip(tmp_path, zip_of(files))
    assert exc_info.value.code == "MALFORMED_DATASET"


def test_extraction_directory_is_removed_after_success_and_failure(tmp_path):
    read_zip(tmp_path, zip_of(write_shapefile(tmp_path / "s")))
    assert list((tmp_path / "work").iterdir()) == []

    files = write_shapefile(tmp_path / "t")
    files["plots.shp"] = b"\x00" * 20
    with pytest.raises(IngestionError):
        read_zip(tmp_path, zip_of(files))
    assert list((tmp_path / "work").iterdir()) == []
