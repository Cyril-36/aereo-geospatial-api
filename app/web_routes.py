"""Serves the workspace: one HTML shell for the app's routes, static files under /static.

The shell is returned for any /files/{id}: the page itself asks the API and shows a
"no file with this ID" state, so a mistyped link still lands somewhere useful.
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).parent / "web"
router = APIRouter(include_in_schema=False)


def _shell() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@router.get("/")
def home() -> FileResponse:
    return _shell()


@router.get("/history")
def history() -> FileResponse:
    return _shell()


@router.get("/files/{file_id}")
def results(file_id: str) -> FileResponse:
    return _shell()


def install_web(app: FastAPI) -> None:
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
