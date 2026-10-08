import datetime as dt
import logging
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.files import _get_record, file_info
from app.database import get_db
from app.errors import ApiError
from app.models import FileRecord, FileStatus
from app.schemas import HistoryPage, Pagination
from app.services.cleanup import sweep_orphan_uploads

logger = logging.getLogger("aereo")
router = APIRouter(prefix="/api")
STATUSES = (FileStatus.COMPLETED, FileStatus.FAILED, FileStatus.PROCESSING, FileStatus.EXTRACTED)
FORMATS = ("SHAPEFILE", "KML")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _one(request: Request, name: str) -> str | None:
    values = request.query_params.getlist(name)
    if len(values) > 1:
        raise ApiError(
            422,
            "DUPLICATE_QUERY_PARAMETER",
            f"Query parameter '{name}' was sent {len(values)} times; send it at most once.",
        )
    return values[0] if values else None


def _int(request: Request, name: str, default: int) -> int:
    raw = _one(request, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ApiError(422, "INVALID_PAGINATION", f"{name} must be an integer.") from None


@router.get("/files/", response_model=HistoryPage)
def list_files(request: Request, db: Session = Depends(get_db)) -> HistoryPage:
    s = request.app.state.settings
    q, status, fmt = (_one(request, n) for n in ("q", "status", "format"))
    since_raw = _one(request, "since")
    since = None
    if since_raw:
        try:
            since = dt.datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
            if since.tzinfo is None:
                raise ValueError("timezone required")
        except ValueError:
            raise ApiError(
                422, "INVALID_QUERY", "since must be an ISO timestamp with a timezone."
            ) from None
    limit = _int(request, "limit", min(s.history_default_page_size, s.history_max_page_size))
    offset = _int(request, "offset", 0)
    if not 1 <= limit <= s.history_max_page_size:
        raise ApiError(
            422, "INVALID_PAGINATION", f"limit must be between 1 and {s.history_max_page_size}."
        )
    if offset < 0:
        raise ApiError(422, "INVALID_PAGINATION", "offset must be 0 or greater.")
    if status and status not in STATUSES:
        raise ApiError(422, "INVALID_QUERY", f"status must be one of: {', '.join(STATUSES)}.")
    if fmt and fmt not in FORMATS:
        raise ApiError(422, "INVALID_QUERY", f"format must be one of: {', '.join(FORMATS)}.")
    if q is not None and len(q) > s.max_search_chars:
        raise ApiError(422, "INVALID_QUERY", f"q exceeds {s.max_search_chars} characters.")

    stmt = select(FileRecord.id, FileRecord.original_filename).order_by(
        FileRecord.created_at.desc(), FileRecord.id
    )
    if status:
        stmt = stmt.where(FileRecord.status == status)
    if fmt:
        stmt = stmt.where(FileRecord.format == fmt)
    if since:
        stmt = stmt.where(FileRecord.created_at >= since)
    rows = db.execute(stmt).all()
    needle = _norm(q.strip()) if q and q.strip() else None
    ids = [i for i, name in rows if needle is None or needle in _norm(f"{name} {i}")]
    window = ids[offset : offset + limit]
    records = {r.id: r for r in db.scalars(select(FileRecord).where(FileRecord.id.in_(window)))}
    total, returned = len(ids), len(window)
    return HistoryPage(
        pagination=Pagination(
            limit=limit,
            offset=offset,
            total=total,
            returned=returned,
            next_offset=offset + returned if offset + returned < total else None,
        ),
        files=[file_info(records[i]) for i in window],
    )


@router.delete("/files/{file_id}/", status_code=204, response_class=Response)
def delete_file(request: Request, file_id: str, db: Session = Depends(get_db)) -> Response:
    record = _get_record(db, file_id)
    if record.status == FileStatus.PROCESSING:
        raise ApiError(409, "FILE_NOT_READY", "The file is still being processed.", record.id)
    stored = Path(record.storage_path)
    # Database cascade avoids loading every feature and its geometry just to delete it.
    db.execute(delete(FileRecord).where(FileRecord.id == record.id))
    db.commit()
    try:
        stored.unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "file %s deleted; its upload could not be removed (cleanup pending)", file_id
        )
        try:
            sweep_orphan_uploads(
                request.app.state.session_factory, request.app.state.settings.upload_dir
            )
        except Exception:
            # Deletion already committed. Cleanup is best-effort and retried at startup.
            logger.warning("orphan cleanup unavailable; cleanup pending")
    return Response(status_code=204)
