"""Opt-in feature filtering and sorting, shared by measurements, position and export.

A request that uses none of these parameters gets exactly the original behaviour: file
order, no filters, and no ``query`` echo in the response.
"""

from dataclasses import asdict, dataclass

from fastapi import Request
from sqlalchemy import case, func

from app.errors import ApiError
from app.models import FeatureRecord
from app.services.measurements import Status
from app.services.search import normalize

QUERY_PARAMS = ("q", "geometry", "status", "warnings", "sort", "order")
GEOMETRY_GROUPS = {
    "polygon": ("Polygon", "MultiPolygon"),
    "line": ("LineString", "MultiLineString"),
    "point": ("Point", "MultiPoint"),
}
GROUPED_TYPES = tuple(t for types in GEOMETRY_GROUPS.values() for t in types)
STATUS_GROUPS = ("measured", "not_applicable", "attention")
RAW_STATUSES = tuple(v for k, v in vars(Status).items() if k.isupper())
SORTS = ("index", "name", "status", "area", "length", "warnings")


@dataclass(frozen=True)
class FeatureQuery:
    q: str | None = None
    geometry: str | None = None
    status: str | None = None
    warnings: str | None = None
    sort: str = "index"
    order: str = "asc"
    present: bool = False  # True when any QUERY_PARAMS name was in the request

    def echo(self) -> dict:
        data = asdict(self)
        del data["present"]
        return data

    @property
    def filtered(self) -> bool:
        return any((self.q, self.geometry, self.status, self.warnings))


def single_query_value(request: Request, name: str) -> str | None:
    values = request.query_params.getlist(name)
    if len(values) > 1:
        raise ApiError(
            422,
            "DUPLICATE_QUERY_PARAMETER",
            f"Query parameter '{name}' was sent {len(values)} times; send it at most once.",
        )
    return values[0] if values else None


def _choice(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is None or value == "":
        return None
    if value not in allowed:
        raise ApiError(422, "INVALID_QUERY", f"{name} must be one of: {', '.join(allowed)}.")
    return value


def feature_query(request: Request) -> FeatureQuery:
    """FastAPI dependency: parse and validate the optional filter and sort parameters."""
    raw = {name: single_query_value(request, name) for name in QUERY_PARAMS}
    max_chars = request.app.state.settings.max_search_chars
    q = raw["q"]
    if q is not None and len(q) > max_chars:
        raise ApiError(422, "INVALID_QUERY", f"q exceeds {max_chars} characters.")
    return FeatureQuery(
        q=(q.strip() or None) if q is not None else None,
        geometry=_choice("geometry", raw["geometry"], (*GEOMETRY_GROUPS, "other")),
        status=_choice("status", raw["status"], (*STATUS_GROUPS, *RAW_STATUSES)),
        warnings=_choice("warnings", raw["warnings"], ("with", "without")),
        sort=_choice("sort", raw["sort"], SORTS) or "index",
        order=_choice("order", raw["order"], ("asc", "desc")) or "asc",
        present=any(value is not None for value in raw.values()),
    )


def filter_clauses(file_id: str, fq: FeatureQuery) -> list:
    f = FeatureRecord
    clauses = [f.file_id == file_id]
    if fq.q:
        # instr() is an exact substring test: no LIKE wildcards to escape.
        clauses.append(func.instr(f.search_text, normalize(fq.q)) > 0)
    if fq.geometry == "other":
        clauses.append(f.geometry_type.is_(None) | f.geometry_type.not_in(GROUPED_TYPES))
    elif fq.geometry:
        clauses.append(f.geometry_type.in_(GEOMETRY_GROUPS[fq.geometry]))
    if fq.status == "measured":
        clauses.append(f.status == Status.MEASURED)
    elif fq.status == "not_applicable":
        clauses.append(f.status == Status.NOT_APPLICABLE)
    elif fq.status == "attention":
        clauses.append(f.status.not_in((Status.MEASURED, Status.NOT_APPLICABLE)))
    elif fq.status:
        clauses.append(f.status == fq.status)
    if fq.warnings == "with":
        clauses.append(f.warning_count > 0)
    elif fq.warnings == "without":
        clauses.append(f.warning_count == 0)
    return clauses


def order_clauses(fq: FeatureQuery) -> list:
    """ORDER BY for a query: missing values last in both directions, ties by file order."""
    f = FeatureRecord
    if fq.sort == "index":
        return [f.index.desc() if fq.order == "desc" else f.index.asc()]
    status_rank = case(
        (f.status == Status.MEASURED, 0), (f.status == Status.NOT_APPLICABLE, 1), else_=2
    )
    keys = {
        "name": [f.sort_name],
        "status": [status_rank, f.status],
        "area": [f.area_m2],
        "length": [f.length_m],
        "warnings": [f.warning_count],
    }[fq.sort]
    ordered = []
    for key in keys:
        ordered.append(key.is_(None))
        ordered.append(key.desc() if fq.order == "desc" else key.asc())
    ordered.append(f.index.asc())
    return ordered
