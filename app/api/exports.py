"""Streams every matching feature as CSV or JSON from one read snapshot."""

import csv
import datetime as dt
import io
import json
import re
from collections.abc import Iterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.files import _feature_out, _get_record, _require_completed, file_info
from app.api.params import FeatureQuery, feature_query, filter_clauses, order_clauses
from app.errors import ApiError
from app.models import FeatureRecord

router = APIRouter(prefix="/api")
CSV_COLUMNS = [
    "index",
    "source_id",
    "name",
    "geometry_type",
    "status",
    "reason_code",
    "reason",
    "area_m2",
    "length_m",
    "measurement_method",
    "measurement_crs",
    "geodesic_relative_difference",
    "warning_codes",
    "warning_messages",
    "folder_path",
]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")


def _text(value) -> str:
    if value is None:
        return ""
    s = str(value)
    return (
        "'" + s if s.startswith(FORMULA_START) or s.lstrip().startswith(("=", "+", "-", "@")) else s
    )


def _num(value) -> str:
    return "" if value is None else repr(float(value))


def _csv_line(values: list[str]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerow(values)
    return buf.getvalue().encode("utf-8")


def _csv_row(f: FeatureRecord) -> bytes:
    warnings = f.warnings or []
    return _csv_line(
        [
            str(f.index),
            _text(f.source_id),
            _text(f.display_name),
            _text(f.geometry_type),
            _text(f.status),
            _text(f.reason_code),
            _text(f.reason),
            _num(f.area_m2),
            _num(f.length_m),
            _text(f.measurement_method),
            _text(f.measurement_crs),
            _num(f.relative_difference),
            _text(" ".join(w["code"] for w in warnings)),
            _text(" | ".join(w["message"] for w in warnings)),
            _text(" / ".join(f.folder_path or [])),
        ]
    )


def export_chunks(
    session, record_id: str, fq: FeatureQuery, fmt: str, batch: int = 500
) -> Iterator[bytes]:
    """Yield the export; every read happens in one transaction (one WAL snapshot)."""
    try:
        # The endpoint establishes the snapshot before returning headers, so a delete
        # between validation and streaming cannot turn an export into an empty 200.
        if not session.in_transaction():
            session.begin()
        record = _get_record(session, record_id)
        _require_completed(record)
        stmt = (
            select(FeatureRecord)
            .where(*filter_clauses(record_id, fq))
            .order_by(*order_clauses(fq))
            .execution_options(yield_per=batch)
        )
        scope = "filtered" if _is_filtered(fq) else "all"
        if fmt == "csv":
            yield "﻿".encode() + _csv_line(CSV_COLUMNS)
            for part in session.scalars(stmt).partitions(batch):
                yield b"".join(_csv_row(f) for f in part)
            return
        features = session.scalars(stmt)
        head = {
            "file": file_info(record).model_dump(mode="json"),
            "export": {
                "scope": scope,
                "filters": fq.echo(),
                "sort": fq.sort,
                "generated_at": dt.datetime.now(dt.UTC).isoformat(),
                "units": {"area": "m2", "length": "m"},
            },
        }
        yield b'{"file":' + json.dumps(head["file"], ensure_ascii=False).encode()
        count, first = 0, True
        yield b',"features":['
        for part in features.partitions(batch):
            out = []
            for f in part:
                out.append((b"" if first else b",") + _feature_out(f).model_dump_json().encode())
                first = False
                count += 1
            yield b"".join(out)
        head["export"]["feature_count"] = count
        yield b'],"export":' + json.dumps(head["export"], ensure_ascii=False).encode() + b"}"
    finally:
        session.close()


def _is_filtered(fq: FeatureQuery) -> bool:
    return any((fq.q, fq.geometry, fq.status, fq.warnings))


def _disposition(filename: str, scope: str, ext: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "export"
    if ascii_stem != stem:
        ascii_stem = "export"
    name = f"{stem}-{scope}.{ext}"
    return f"attachment; filename=\"{ascii_stem}-{scope}.{ext}\"; filename*=UTF-8''{quote(name)}"


@router.get("/files/{file_id}/export/")
def export(request: Request, file_id: str, fq: FeatureQuery = Depends(feature_query)):
    formats = request.query_params.getlist("format")
    if len(formats) > 1:
        raise ApiError(
            422,
            "DUPLICATE_QUERY_PARAMETER",
            f"Query parameter 'format' was sent {len(formats)} times; send it at most once.",
        )
    fmt = formats[0] if formats else None
    if fmt not in ("csv", "json"):
        raise ApiError(422, "INVALID_QUERY", "format must be one of: csv, json.")
    session = request.app.state.session_factory()
    try:
        record = _get_record(session, file_id)
        _require_completed(record)
        filename, record_id = record.original_filename, record.id
    except BaseException:
        session.close()
        raise
    media = "text/csv; charset=utf-8" if fmt == "csv" else "application/json"
    scope = "filtered" if _is_filtered(fq) else "all"
    return StreamingResponse(
        export_chunks(session, record_id, fq, fmt),
        media_type=media,
        headers={"Content-Disposition": _disposition(filename, scope, fmt)},
        background=BackgroundTask(session.close),
    )
