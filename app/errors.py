"""Error codes, the domain exception and the API error envelope."""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("aereo")

# Ingestion error code -> HTTP status. Services raise IngestionError with a code;
# only the API layer knows about HTTP.
ERROR_STATUS: dict[str, int] = {
    "UPLOAD_TOO_LARGE": 413,
    "EXPANDED_SIZE_EXCEEDED": 413,
    "TOO_MANY_ARCHIVE_MEMBERS": 413,
    "TOO_MANY_FEATURES": 413,
    "TOO_MANY_VERTICES": 413,
    "UNSUPPORTED_FORMAT": 415,
    "KMZ_UNSUPPORTED": 415,
    "EMPTY_FILE": 422,
    "INVALID_ZIP": 422,
    "UNSAFE_ARCHIVE_PATH": 422,
    "ARCHIVE_SYMLINK": 422,
    "ENCRYPTED_ARCHIVE_ENTRY": 422,
    "DUPLICATE_ARCHIVE_PATH": 422,
    "NESTED_ARCHIVE": 422,
    "NO_SHAPEFILE": 422,
    "MULTIPLE_DATASETS_UNSUPPORTED": 422,
    "MISSING_SHAPEFILE_COMPONENT": 422,
    "MALFORMED_DATASET": 422,
    "INVALID_KML": 422,
    "UNSAFE_XML": 422,
    "INVALID_SOURCE_CRS": 422,
    "CRS_CONFLICT": 422,
}


class IngestionError(Exception):
    """A file cannot be ingested. ``code`` is a stable machine-readable identifier."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ApiError(StarletteHTTPException):
    """An error rendered with the standard envelope.

    Subclasses Starlette's HTTPException on purpose: FastAPI re-raises those unchanged
    even when they occur while the request body is being parsed.
    """

    def __init__(self, status_code: int, code: str, message: str, file_id: str | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.file_id = file_id

    @classmethod
    def from_ingestion(cls, exc: IngestionError, file_id: str | None = None) -> "ApiError":
        return cls(ERROR_STATUS.get(exc.code, 422), exc.code, exc.message, file_id)


def error_body(code: str, message: str, file_id: str | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if file_id is not None:
        error["file_id"] = file_id
    return {"error": error}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.file_id), exc.status_code)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(error_body(code, str(exc.detail)), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        return JSONResponse(
            error_body("VALIDATION_ERROR", f"Invalid request: {', '.join(fields)}"), 422
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        request_id = uuid.uuid4().hex
        logger.exception("unhandled error request_id=%s", request_id)
        return JSONResponse(
            error_body("INTERNAL_ERROR", f"Unexpected server error (request id {request_id})."),
            500,
        )
