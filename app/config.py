"""Application settings. Every limit is overridable with an ``AEREO_`` environment variable."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEREO_")

    data_dir: Path = Path("data")
    database_url: str | None = None  # defaults to SQLite inside data_dir

    # Ingestion budgets (plan v3, section 5).
    max_upload_bytes: int = 10 * MIB
    max_expanded_bytes: int = 100 * MIB
    max_archive_members: int = 1_000
    max_features: int = 20_000
    max_vertices: int = 1_000_000
    # Multipart framing allowance on top of the file itself, for the request-body guard.
    multipart_overhead_bytes: int = 64 * 1024

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
