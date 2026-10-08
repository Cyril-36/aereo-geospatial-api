"""Limits and settings the web workspace shows instead of hard-coding them."""

from fastapi import APIRouter, Request

from app.schemas import ConfigOut

router = APIRouter(prefix="/api")


@router.get("/config/", response_model=ConfigOut)
def get_config(request: Request) -> ConfigOut:
    settings = request.app.state.settings
    return ConfigOut(
        max_upload_bytes=settings.max_upload_bytes,
        max_features=settings.max_features,
        default_page_size=min(settings.default_page_size, settings.max_page_size),
        max_page_size=settings.max_page_size,
        accepted_extensions=[".zip", ".kml"],
        map_tile_url=settings.map_tile_url,
        map_max_features=settings.map_max_features,
        map_max_vertices=settings.map_max_vertices,
    )
