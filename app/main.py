import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import update

from app.api.config_route import router as config_router
from app.api.files import router as files_router
from app.config import Settings, get_settings
from app.database import make_engine, make_session_factory
from app.errors import install_error_handlers
from app.middleware import BodySizeLimitMiddleware
from app.migrations import migrate
from app.models import FileRecord, FileStatus, utcnow
from app.web_routes import install_web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def recover_interrupted(session_factory) -> int:
    """Single-process deployment: any PROCESSING row at startup was interrupted mid-request."""
    with session_factory() as session:
        result = session.execute(
            update(FileRecord)
            .where(FileRecord.status == FileStatus.PROCESSING)
            .values(
                status=FileStatus.FAILED,
                error_code="INTERRUPTED",
                error_message="Processing was interrupted by a server restart.",
                processed_at=utcnow(),
            )
        )
        session.commit()
        return result.rowcount


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.resolved_database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for directory in (settings.data_dir, settings.upload_dir, settings.work_dir):
            directory.mkdir(parents=True, exist_ok=True)
        migrate(engine)
        recover_interrupted(session_factory)
        yield
        engine.dispose()

    app = FastAPI(
        title="AEREO Geospatial File Measurement API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.processing_slots = threading.BoundedSemaphore(settings.max_concurrent_processing)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_upload_bytes + settings.multipart_overhead_bytes,
        file_limit_bytes=settings.max_upload_bytes,
    )
    install_error_handlers(app)
    app.include_router(files_router)
    app.include_router(config_router)
    install_web(app)
    return app


app = create_app()
