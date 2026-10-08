import pytest
from pyproj import CRS
from pyproj.transformer import TransformerGroup

from app.errors import IngestionError
from app.services import crs
from app.services.dataset import SourceCrs

ENGINEERING_WKT = (
    'ENGCRS["Site grid",EDATUM["Site"],CS[Cartesian,2],'
    'AXIS["x",east,LENGTHUNIT["metre",1]],AXIS["y",north,LENGTHUNIT["metre",1]]]'
)
# What GDAL writes as the .prj for EPSG:4326 (ESRI flavour).
ESRI_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)


def known(code: str) -> SourceCrs:
    return SourceCrs("KNOWN", code, CRS.from_user_input(code).to_wkt(), "PRJ_FILE")


UNKNOWN = SourceCrs("UNKNOWN", None, None, "NONE")


def test_kml_is_wgs84():
    resolved = crs.resolve(crs.kml_crs())
    assert resolved.status == "OK"
    assert resolved.is_geographic
    assert resolved.transformation.accuracy_m == 0.0
    assert resolved.warnings == []


def test_unknown_stays_unknown_and_is_not_measured():
    resolved = crs.resolve(UNKNOWN)
    assert resolved.status == "UNKNOWN_CRS"
    assert resolved.to_wgs84 is None


def test_unparseable_prj_is_unsupported():
    resolved = crs.resolve(crs.from_prj("not a crs"))
    assert (resolved.status, resolved.reason_code) == ("CRS_UNSUPPORTED", "CRS_UNPARSEABLE")


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (SourceCrs("KNOWN", "Site grid", ENGINEERING_WKT, "PRJ_FILE"), "CRS_NOT_HORIZONTAL"),
        (known("EPSG:4978"), "CRS_NOT_HORIZONTAL"),  # geocentric
        (known("IAU_2015:49900"), "NO_TRANSFORMATION"),  # Mars: no path to WGS84
    ],
)
def test_untransformable_crs_is_unsupported(source, code):
    resolved = crs.resolve(source)
    assert (resolved.status, resolved.reason_code) == ("CRS_UNSUPPORTED", code)


def test_override_supplies_missing_crs():
    source = crs.apply_override(UNKNOWN, "EPSG:32643")
    assert (source.status, source.identifier, source.origin) == ("KNOWN", "EPSG:32643", "OVERRIDE")


@pytest.mark.parametrize("text", ["32643", "epsg:32643", " EPSG : 32643 "])
def test_override_epsg_spellings(text):
    assert crs.apply_override(UNKNOWN, text).identifier == "EPSG:32643"


def test_override_as_wkt():
    source = crs.apply_override(UNKNOWN, CRS.from_epsg(2263).to_wkt())
    assert source.identifier == "EPSG:2263"


def test_override_replaces_unparseable_prj():
    source = crs.apply_override(crs.from_prj("garbage"), "EPSG:4326")
    assert (source.status, source.origin) == ("KNOWN", "OVERRIDE")


def test_override_matching_esri_prj_is_not_a_conflict():
    # An ESRI .prj differs from EPSG:4326 only in axis-order metadata.
    declared = crs.from_prj(ESRI_WGS84)
    assert crs.apply_override(declared, "EPSG:4326") is declared


@pytest.mark.parametrize(
    ("declared", "override"),
    [(known("EPSG:32643"), "EPSG:32644"), (crs.kml_crs(), "EPSG:32643")],
)
def test_conflicting_override_is_rejected(declared, override):
    with pytest.raises(IngestionError) as exc_info:
        crs.apply_override(declared, override)
    assert exc_info.value.code == "CRS_CONFLICT"


@pytest.mark.parametrize("text", ["EPSG:999999", "not a crs", "PROJCS[broken"])
def test_invalid_override_is_rejected(text):
    with pytest.raises(IngestionError) as exc_info:
        crs.apply_override(UNKNOWN, text)
    assert exc_info.value.code == "INVALID_SOURCE_CRS"


def test_feet_crs_reports_its_unit():
    resolved = crs.resolve(known("EPSG:2263"))
    assert not resolved.is_geographic
    assert resolved.metres_per_unit == pytest.approx(1200 / 3937)  # US survey foot, exact


def test_kalianpur_1975_records_22m_helmert():
    resolved = crs.resolve(known("EPSG:4146"))
    assert resolved.status == "OK"
    assert "Kalianpur 1975 to WGS 84" in resolved.transformation.operation
    assert resolved.transformation.accuracy_m == 22.0  # EPSG-registered accuracy
    assert resolved.transformation.best_available
    assert resolved.warnings == []


def test_web_mercator_is_a_same_datum_conversion_without_accuracy_warning():
    # PROJ states no accuracy for this conversion, but no datum change is involved.
    resolved = crs.resolve(known("EPSG:3857"))
    assert resolved.transformation.accuracy_m is None
    assert resolved.warnings == []


def test_ballpark_datum_shift_warns_accuracy_unknown():
    source = known("+proj=longlat +ellps=intl +no_defs +type=crs")
    resolved = crs.resolve(source)
    assert "Ballpark" in resolved.transformation.operation
    assert [w["code"] for w in resolved.warnings] == ["TRANSFORMATION_ACCURACY_UNKNOWN"]


def test_nad27_without_grids_uses_best_available_and_warns():
    # Environment fact (no PROJ grids installed, network disabled), checked independently:
    assert TransformerGroup("EPSG:4267", "EPSG:4326").best_available is False
    resolved = crs.resolve(known("EPSG:4267"))
    assert resolved.status == "OK"
    assert not resolved.transformation.best_available
    assert resolved.transformation.accuracy_m is not None
    assert [w["code"] for w in resolved.warnings] == ["NON_BEST_TRANSFORMATION"]


def test_non_best_warning_is_deterministic(monkeypatch):
    class FakeTransformer:
        description = "fallback op"
        accuracy = 5.0

    class FakeOperation:
        name = "grid op"

    class FakeGroup:
        def __init__(self, *args, **kwargs):
            self.transformers = [FakeTransformer()]
            self.unavailable_operations = [FakeOperation()]
            self.best_available = False

    monkeypatch.setattr(crs, "TransformerGroup", FakeGroup)
    resolved = crs.resolve(known("EPSG:32643"))
    assert resolved.transformation.operation == "fallback op"
    assert resolved.transformation.accuracy_m == 5.0
    assert "grid op" in resolved.warnings[0]["message"]


def test_no_available_operation_is_unsupported(monkeypatch):
    class EmptyGroup:
        def __init__(self, *args, **kwargs):
            self.transformers = []
            self.unavailable_operations = [object()]
            self.best_available = False

    monkeypatch.setattr(crs, "TransformerGroup", EmptyGroup)
    resolved = crs.resolve(known("EPSG:32643"))
    assert (resolved.status, resolved.reason_code) == ("CRS_UNSUPPORTED", "NO_TRANSFORMATION")
    assert "grids" in resolved.reason


def test_network_is_disabled():
    from pyproj import network

    assert network.is_network_enabled() is False
