"""Application settings. Every limit is overridable with an ``AEREO_`` environment variable."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEREO_")

    data_dir: Path = Path("data")
    database_url: str | None = None  # defaults to SQLite inside data_dir

    # Ingestion budgets.
    max_upload_bytes: int = 10 * MIB
    max_expanded_bytes: int = 100 * MIB
    max_archive_members: int = 1_000
    max_features: int = 20_000
    max_vertices: int = 1_000_000
    # Multipart framing allowance on top of the file itself, for the request-body guard.
    multipart_overhead_bytes: int = 64 * 1024

    # Measurement.
    densify_max_segment_m: float = 50_000.0
    max_vertices_per_feature: int = 200_000  # original + densified, per feature
    geodesic_relative_tolerance: float = 0.001  # GEODESIC_DISAGREEMENT above 0.1 %

    # API.
    default_page_size: int = 100
    max_page_size: int = 1_000
    max_source_crs_chars: int = 20_000
    history_default_page_size: int = 50
    history_max_page_size: int = 200
    max_search_chars: int = 200

    # Web workspace. An empty tile URL turns the basemap off (vectors still draw).
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    map_max_features: int = 5_000
    map_max_vertices: int = 250_000

    max_concurrent_processing: int = 2

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'aereo.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
