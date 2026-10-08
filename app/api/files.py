import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, IngestionError
from app.models import FeatureRecord, FileRecord, FileStatus, utcnow
from app.results import apply_results, geodesic_reference, geometry_out
from app.schemas import FeatureMeasurement, FileInfo, MeasurementsPage, Message, Pagination
from app.services.crs import parse_override
from app.services.processor import process
from app.services.storage import classify_filename, display_filename, save_stream

logger = logging.getLogger("aereo")
router = APIRouter(prefix="/api")


def file_info(record: FileRecord) -> FileInfo:
    return FileInfo(
        id=record.id,
        filename=record.original_filename,
        format=record.format,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        status=record.status,
        feature_count=record.feature_count,
        counts=record.status_counts,
        crs=record.crs,
        crs_status=record.crs_status,
        crs_origin=record.crs_origin,
        transformation=record.transformation,
        warnings=[Message(**w) for w in record.warnings or []],
        error=Message(code=record.error_code, message=record.error_message or "")
        if record.error_code
        else None,
        created_at=record.created_at,
        processed_at=record.processed_at,
    )


def _validated_source_crs(value: str | None, max_chars: int) -> str | None:
    if value is None or not value.strip():
        return None
    if len(value) > max_chars:
        raise ApiError(422, "INVALID_SOURCE_CRS", f"source_crs exceeds {max_chars} characters.")
    try:
        parse_override(value)
    except IngestionError as exc:
        raise ApiError.from_ingestion(exc) from exc
    return value.strip()


@router.post("/files/", status_code=201, response_model=FileInfo)
def upload_file(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="A .zip containing one Shapefile, or a .kml file."),
    source_crs: str | None = Form(
        None,
        description="Optional source CRS (EPSG code or WKT). Supplies a missing .prj; must "
        "match a declared CRS, else 422 CRS_CONFLICT.",
    ),
    db: Session = Depends(get_db),
) -> FileInfo:
    settings = request.app.state.settings
    filename = display_filename(file.filename)
    try:
        file_format = classify_filename(filename)
    except IngestionError as exc:
        raise ApiError.from_ingestion(exc) from exc
    override = _validated_source_crs(source_crs, settings.max_source_crs_chars)

    file_id = str(uuid.uuid4())
    suffix = ".zip" if file_format == "SHAPEFILE" else ".kml"
    try:
        stored = save_stream(
            file.file, settings.upload_dir / f"{file_id}{suffix}", settings.max_upload_bytes
        )
    except IngestionError as exc:
        raise ApiError.from_ingestion(exc) from exc

    record = FileRecord(
        id=file_id,
        original_filename=filename,
        format=file_format,
        storage_path=str(stored.path),
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        status=FileStatus.PROCESSING,
        source_crs_input=override,
        warnings=[],
    )
    db.add(record)
    db.commit()

    with request.app.state.processing_slots:
        try:
            processed = process(Path(stored.path), file_format, settings, override)
        except IngestionError as exc:
            _mark_failed(db, record, exc.code, exc.message)
            logger.info("file %s failed: %s", file_id, exc.code)
            raise ApiError.from_ingestion(exc, file_id) from exc
        except Exception:
            _mark_failed(db, record, "INTERNAL_ERROR", "Unexpected error while processing.")
            raise

    # Every feature and the COMPLETED status are committed in a single transaction.
    try:
        apply_results(record, processed)
        record.status = FileStatus.COMPLETED
        record.processed_at = utcnow()
        db.commit()
    except Exception:
        logger.exception("file %s: storing results failed", file_id)
        _mark_failed(db, record, "PERSISTENCE_FAILED", "Results could not be stored.")
        raise

    logger.info(
        "file %s completed: %d features %s", file_id, record.feature_count, record.status_counts
    )
    response.headers["Location"] = f"/api/files/{file_id}/"
    return file_info(record)


def _mark_failed(db: Session, record: FileRecord, code: str, message: str) -> None:
    """Record a failure without partial results. Never raises: the original error matters
    more, and a row left PROCESSING is marked INTERRUPTED at the next start."""
    file_id = record.id
    try:
        db.rollback()
        db.expunge_all()
        stored = db.get(FileRecord, file_id)
        if stored is None:
            return
        stored.status = FileStatus.FAILED
        stored.error_code = code
        stored.error_message = message
        stored.processed_at = utcnow()
        db.commit()
    except Exception:
        logger.exception("file %s: could not record failure %s", file_id, code)
        db.rollback()


def _get_record(db: Session, file_id: str) -> FileRecord:
    try:
        key = str(uuid.UUID(file_id))
    except ValueError:
        key = None
    record = db.get(FileRecord, key) if key else None
    if record is None:
        raise ApiError(404, "FILE_NOT_FOUND", f"No file with id {file_id!r}.")
    return record


@router.get("/files/{file_id}/", response_model=FileInfo)
def get_file(file_id: str, db: Session = Depends(get_db)) -> FileInfo:
    return file_info(_get_record(db, file_id))


def _feature_out(feature: FeatureRecord) -> FeatureMeasurement:
    geometry, geometry_crs, origin = geometry_out(feature)
    return FeatureMeasurement(
        index=feature.index,
        source_id=feature.source_id,
        geometry_type=feature.geometry_type,
        geometry=geometry,
        geometry_crs=geometry_crs,
        geometry_origin=origin,
        source_crs=feature.source_crs,
        properties=feature.properties or {},
        folder_path=feature.folder_path or [],
        status=feature.status,
        reason_code=feature.reason_code,
        reason=feature.reason,
        area_m2=feature.area_m2,
        length_m=feature.length_m,
        measurement_method=feature.measurement_method,
        measurement_crs=feature.measurement_crs,
        geodesic_reference=geodesic_reference(feature),
        transformation=feature.transformation,
        generated_vertices=feature.generated_vertices,
        warnings=[Message(**w) for w in feature.warnings or []],
    )


@router.get("/files/{file_id}/measurements/", response_model=MeasurementsPage)
def get_measurements(
    request: Request,
    file_id: str,
    limit: int | None = Query(None, description="Page size (default 100, maximum 1000)"),
    offset: int = Query(0, description="Number of features to skip, in index order"),
    db: Session = Depends(get_db),
) -> MeasurementsPage:
    settings = request.app.state.settings
    record = _get_record(db, file_id)
    if record.status == FileStatus.PROCESSING:
        raise ApiError(409, "FILE_NOT_READY", "The file is still being processed.", record.id)
    if record.status == FileStatus.FAILED:
        raise ApiError(
            409,
            "FILE_FAILED",
            f"Processing failed ({record.error_code}); no measurements exist.",
            record.id,
        )
    if record.status != FileStatus.COMPLETED:
        raise ApiError(
            409,
            "MEASUREMENTS_UNAVAILABLE",
            "This file was stored by an earlier version that did not measure features. "
            "Upload it again to measure it.",
            record.id,
        )

    limit = settings.default_page_size if limit is None else limit
    if not 1 <= limit <= settings.max_page_size:
        raise ApiError(
            422, "INVALID_PAGINATION", f"limit must be between 1 and {settings.max_page_size}."
        )
    if offset < 0:
        raise ApiError(422, "INVALID_PAGINATION", "offset must be 0 or greater.")

    total = db.scalar(
        select(func.count()).select_from(FeatureRecord).where(FeatureRecord.file_id == record.id)
    )
    features = db.scalars(
        select(FeatureRecord)
        .where(FeatureRecord.file_id == record.id)
        .order_by(FeatureRecord.index)
        .limit(limit)
        .offset(offset)
    ).all()
    returned = len(features)
    return MeasurementsPage(
        file_id=record.id,
        status=record.status,
        crs=record.crs,
        feature_count=record.feature_count or 0,
        counts=record.status_counts or {},
        pagination=Pagination(
            limit=limit,
            offset=offset,
            total=total,
            returned=returned,
            next_offset=offset + returned if offset + returned < total else None,
        ),
        features=[_feature_out(f) for f in features],
    )
