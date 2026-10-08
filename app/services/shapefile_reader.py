"""Read one extracted Shapefile dataset with Fiona, preserving geometry and attributes as-is."""

import math

import fiona
from fiona.errors import FionaError
from fiona.model import to_dict

from app.errors import IngestionError
from app.services import crs
from app.services.dataset import (
    Dataset,
    FeatureBudget,
    IngestionLimits,
    RawFeature,
    iter_positions,
    json_safe,
)
from app.services.safe_zip import ShapefileComponents


def read_shapefile(components: ShapefileComponents, limits: IngestionLimits) -> Dataset:
    # The CRS comes only from the .prj text. GDAL reads .cpg itself and decodes attributes.
    prj_text = None
    if components.prj is not None:
        prj_text = components.prj.read_bytes().decode("utf-8", errors="replace")
    source_crs = crs.from_prj(prj_text)

    budget = FeatureBudget(limits)
    features: list[RawFeature] = []
    try:
        with fiona.open(components.shp, driver="ESRI Shapefile") as src:
            declared = len(src)
            if declared > limits.max_features:
                raise IngestionError(
                    "TOO_MANY_FEATURES",
                    f"Shapefile has {declared} features; the limit is {limits.max_features}.",
                )
            for index, record in enumerate(src):
                budget.add_feature()
                features.append(_to_raw_feature(index, record, budget))
    except IngestionError:
        raise
    except (FionaError, OSError, ValueError, RuntimeError) as exc:
        raise IngestionError(
            "MALFORMED_DATASET",
            f"Shapefile {components.archive_name!r} could not be read: {type(exc).__name__}.",
        ) from exc

    if len(features) != declared:
        # Never present a partial read as a complete extraction.
        raise IngestionError(
            "MALFORMED_DATASET",
            f"Shapefile declares {declared} features but {len(features)} could be read.",
        )

    warnings = []
    if source_crs.status == "UNKNOWN":
        warnings.append(
            {"code": "CRS_MISSING", "message": "No .prj file: the source CRS is unknown."}
        )
    elif source_crs.status == "INVALID":
        warnings.append(
            {"code": "CRS_INVALID", "message": "The .prj file could not be parsed as a CRS."}
        )
    return Dataset("SHAPEFILE", source_crs, features, warnings)


def _to_raw_feature(index: int, record, budget: FeatureBudget) -> RawFeature:
    properties = json_safe(dict(record.properties or {}))
    feature = RawFeature(
        index=index,
        source_id=None if record.id is None else str(record.id),
        geometry_type=None,
        geometry=None,
        properties=properties,
    )
    if record.geometry is None:
        return feature

    geometry = to_dict(record.geometry)
    feature.geometry_type = geometry["type"]
    positions = list(iter_positions(geometry))
    budget.add_vertices(len(positions))
    if not all(math.isfinite(v) for pos in positions for v in pos):
        feature.issue_code = "NON_FINITE_COORDINATES"
        feature.issue_detail = "Geometry contains NaN or infinite coordinates."
        return feature
    feature.geometry = _plain(geometry)
    return feature


def _plain(value):
    """Convert Fiona's mapping/tuple structures to plain JSON-ready dicts and lists."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value
