"""Source-CRS description.

Phase 1 only records what the file declares. Resolution with a ``source_crs`` override
and transformation selection arrive with the measurement engine (plan v3, section 4).
"""

from functools import lru_cache

from pyproj import CRS
from pyproj.exceptions import CRSError

from app.services.dataset import SourceCrs

EPSG_MIN_CONFIDENCE = 70


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
    return SourceCrs("KNOWN", "EPSG:4326", CRS.from_epsg(4326).to_wkt(), "KML_SPECIFICATION")
