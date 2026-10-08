"""Source-CRS description, resolution and transformation selection.

The declared CRS is described exactly as the file states it. Resolution optionally applies a
caller-supplied override (EPSG identifier or WKT) and never guesses: a file without a usable
CRS stays UNKNOWN and is never measured. The transformation to WGS84 is chosen with
``TransformerGroup`` and recorded, including its accuracy, so callers can disclose it.
"""

import re
import warnings
from dataclasses import dataclass, field
from functools import lru_cache

from pyproj import CRS, Transformer, network
from pyproj.exceptions import CRSError, ProjError
from pyproj.transformer import TransformerGroup

from app.errors import IngestionError
from app.services.dataset import SourceCrs
from app.services.dataset import warning as feature_warning

# Never download transformation grids at request time; results must not depend on the network.
network.set_network_enabled(active=False)
# pyproj also warns when the best operation needs missing grids; resolve() reports that
# itself as NON_BEST_TRANSFORMATION, so the duplicate log line is suppressed.
warnings.filterwarnings(
    "ignore", message="Best transformation is not available", category=UserWarning, module="pyproj"
)

EPSG_MIN_CONFIDENCE = 70
WGS84 = "EPSG:4326"
_EPSG_CODE = re.compile(r"^\s*(?:EPSG\s*:\s*)?(\d{4,6})\s*$", re.IGNORECASE)


def identify(crs: CRS) -> str:
    epsg = crs.to_epsg(min_confidence=EPSG_MIN_CONFIDENCE)
    return f"EPSG:{epsg}" if epsg is not None else crs.name


def from_prj(wkt: str | None) -> SourceCrs:
    """Describe a ``.prj`` declaration. A missing or blank ``.prj`` is UNKNOWN, never guessed."""
    if wkt is None or not wkt.strip():
        return SourceCrs("UNKNOWN", None, None, "NONE")
    try:
        crs = CRS.from_wkt(wkt)
    except CRSError:
        return SourceCrs("INVALID", None, wkt, "PRJ_FILE")
    return SourceCrs("KNOWN", identify(crs), wkt, "PRJ_FILE")


@lru_cache(maxsize=1)
def kml_crs() -> SourceCrs:
    """OGC KML 2.2 coordinates are always WGS84 longitude/latitude."""
    return SourceCrs("KNOWN", WGS84, CRS.from_epsg(4326).to_wkt(), "KML_SPECIFICATION")


def parse_override(text: str) -> CRS:
    """Parse a caller-supplied CRS: an EPSG identifier (``EPSG:32643`` or ``32643``) or WKT."""
    match = _EPSG_CODE.match(text)
    try:
        return CRS.from_epsg(int(match.group(1))) if match else CRS.from_wkt(text)
    except CRSError as exc:
        raise IngestionError(
            "INVALID_SOURCE_CRS", "source_crs must be an EPSG identifier or WKT."
        ) from exc


def same_crs(a: CRS, b: CRS) -> bool:
    # GDAL writes ESRI-flavoured .prj files whose WGS84 differs from EPSG:4326 only in axis
    # order metadata, so axis order is ignored when comparing declarations.
    return a.equals(b, ignore_axis_order=True)


def apply_override(declared: SourceCrs, override: str | None) -> SourceCrs:
    """Combine the declared CRS with an optional override.

    - No override: the declaration is used as-is (UNKNOWN stays UNKNOWN).
    - Declared CRS is valid: the override must describe the same CRS, else CRS_CONFLICT.
    - Declared CRS is missing or unparseable: the override supplies it.
    """
    if override is None or not override.strip():
        return declared
    override_crs = parse_override(override)
    if declared.status == "KNOWN":
        if not same_crs(CRS.from_wkt(declared.wkt), override_crs):
            raise IngestionError(
                "CRS_CONFLICT",
                f"source_crs {identify(override_crs)} conflicts with the file's declared CRS "
                f"{declared.identifier}.",
            )
        return declared
    return SourceCrs("KNOWN", identify(override_crs), override_crs.to_wkt(), "OVERRIDE")


@dataclass(frozen=True)
class Transformation:
    operation: str
    accuracy_m: float | None  # None: PROJ states no accuracy
    best_available: bool


@dataclass
class ResolvedCrs:
    """A source CRS prepared for measurement, or the reason it cannot be measured."""

    source: SourceCrs
    status: str  # "OK" | "UNKNOWN_CRS" | "CRS_UNSUPPORTED"
    reason_code: str | None = None
    reason: str | None = None
    crs: CRS | None = None
    to_wgs84: Transformer | None = None
    transformation: Transformation | None = None
    is_geographic: bool = False
    metres_per_unit: float | None = None  # horizontal axis unit, for projected sources
    warnings: list[dict[str, str]] = field(default_factory=list)


def _same_datum_as_wgs84(crs: CRS) -> bool:
    geodetic = crs.geodetic_crs
    return geodetic is not None and same_crs(geodetic, CRS.from_epsg(4326))


def resolve(source: SourceCrs) -> ResolvedCrs:
    if source.status == "UNKNOWN":
        return ResolvedCrs(
            source, "UNKNOWN_CRS", "CRS_MISSING", "The source CRS is unknown; nothing is assumed."
        )
    if source.status == "INVALID":
        return ResolvedCrs(
            source, "CRS_UNSUPPORTED", "CRS_UNPARSEABLE", "The declared CRS could not be parsed."
        )

    crs = CRS.from_wkt(source.wkt)
    if crs.is_geocentric or not (crs.is_geographic or crs.is_projected):
        return ResolvedCrs(
            source,
            "CRS_UNSUPPORTED",
            "CRS_NOT_HORIZONTAL",
            f"{crs.type_name} '{crs.name}' is not a geographic or projected CRS.",
            crs=crs,
        )

    try:
        group = TransformerGroup(crs, WGS84, always_xy=True)
    except ProjError as exc:
        return ResolvedCrs(
            source, "CRS_UNSUPPORTED", "NO_TRANSFORMATION", f"PROJ error: {exc}", crs=crs
        )
    if not group.transformers:
        reason = f"No transformation from '{crs.name}' to WGS84 is available"
        if group.unavailable_operations:
            reason += " (the required grids are not installed)"
        return ResolvedCrs(source, "CRS_UNSUPPORTED", "NO_TRANSFORMATION", reason + ".", crs=crs)

    # PROJ orders operations best first; take the best one that can actually run here.
    transformer = group.transformers[0]
    accuracy = transformer.accuracy if transformer.accuracy >= 0 else None
    warnings: list[dict[str, str]] = []
    if not group.best_available:
        best = group.unavailable_operations[0]
        stated = "unknown" if accuracy is None else f"{accuracy:g} m"
        warnings.append(
            feature_warning(
                "NON_BEST_TRANSFORMATION",
                f"Best transformation '{best.name}' needs grids that are not installed; using "
                f"'{transformer.description}' (accuracy {stated}).",
            )
        )
    if accuracy is None and not _same_datum_as_wgs84(crs):
        # A datum change with no stated accuracy, typically a ballpark transformation that
        # omits the datum shift. That moves geometry; it barely changes its size.
        warnings.append(
            feature_warning(
                "TRANSFORMATION_ACCURACY_UNKNOWN",
                f"Transformation '{transformer.description}' has no stated accuracy; positions "
                "may be off by up to hundreds of metres.",
            )
        )

    return ResolvedCrs(
        source,
        "OK",
        crs=crs,
        to_wgs84=transformer,
        transformation=Transformation(
            transformer.description, accuracy, bool(group.best_available)
        ),
        is_geographic=crs.is_geographic,
        metres_per_unit=None if crs.is_geographic else crs.axis_info[0].unit_conversion_factor,
        warnings=warnings,
    )
