import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, IngestionError
from app.models import FeatureRecord, FileRecord, FileStatus, utcnow
from app.schemas import FileInfo, Message
from app.services.dataset import Dataset
from app.services.processor import extract, limits_from
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
        crs=record.crs,
        crs_status=record.crs_status,
        warnings=[Message(**w) for w in record.warnings or []],
        error=Message(code=record.error_code, message=record.error_message or "")
        if record.error_code
        else None,
        created_at=record.created_at,
        processed_at=record.processed_at,
    )


def _store_dataset(record: FileRecord, dataset: Dataset) -> None:
    record.crs_status = dataset.source_crs.status
    record.crs = dataset.source_crs.identifier
    record.crs_wkt = dataset.source_crs.wkt
    record.crs_origin = dataset.source_crs.origin
    record.feature_count = len(dataset.features)
    record.warnings = dataset.warnings
    record.features = [
        FeatureRecord(
            index=f.index,
            source_id=f.source_id,
            geometry_type=f.geometry_type,
            source_geometry=f.geometry,
            source_crs=dataset.source_crs.identifier,
            properties=f.properties,
            folder_path=f.folder_path,
            issue_code=f.issue_code,
            issue_detail=f.issue_detail,
            warnings=f.warnings,
        )
        for f in dataset.features
    ]


@router.post("/files/", status_code=201, response_model=FileInfo)
def upload_file(
    request: Request,
    response: Response,
    file: UploadFile = File(..., description="A .zip containing one Shapefile, or a .kml file."),
    db: Session = Depends(get_db),
) -> FileInfo:
    settings = request.app.state.settings
    filename = display_filename(file.filename)
    try:
        file_format = classify_filename(filename)
    except IngestionError as exc:
        raise ApiError.from_ingestion(exc) from exc

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
        warnings=[],
    )
    db.add(record)
    db.commit()

    with request.app.state.processing_slots:
        try:
            dataset = extract(
                Path(stored.path), file_format, limits_from(settings), settings.work_dir
            )
        except IngestionError as exc:
            _fail(db, record, exc.code, exc.message)
            logger.info("file %s failed: %s", file_id, exc.code)
            raise ApiError.from_ingestion(exc, file_id) from exc
        except Exception:
            _fail(db, record, "INTERNAL_ERROR", "Unexpected error while processing the file.")
            raise

    # Features and the status change are committed in one transaction.
    _store_dataset(record, dataset)
    record.status = FileStatus.EXTRACTED
    record.processed_at = utcnow()
    db.commit()
    logger.info("file %s extracted: %d features", file_id, record.feature_count)

    response.headers["Location"] = f"/api/files/{file_id}/"
    return file_info(record)


def _fail(db: Session, record: FileRecord, code: str, message: str) -> None:
    db.rollback()
    record.status = FileStatus.FAILED
    record.error_code = code
    record.error_message = message
    record.processed_at = utcnow()
    db.add(record)
    db.commit()


@router.get("/files/{file_id}/", response_model=FileInfo)
def get_file(file_id: str, db: Session = Depends(get_db)) -> FileInfo:
    try:
        key = str(uuid.UUID(file_id))
    except ValueError:
        key = None
    record = db.get(FileRecord, key) if key else None
    if record is None:
        raise ApiError(404, "FILE_NOT_FOUND", f"No file with id {file_id!r}.")
    return file_info(record)
