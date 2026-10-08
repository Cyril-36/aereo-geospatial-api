"""Removes stored uploads that no file record references (e.g. after a failed unlink)."""

import logging
import time
from pathlib import Path

from sqlalchemy import select

from app.models import FileRecord

logger = logging.getLogger("aereo")


def sweep_orphan_uploads(
    session_factory, upload_dir: Path, min_age_s: float = 600, now: float | None = None
) -> list[Path]:
    if not upload_dir.is_dir():
        return []
    with session_factory() as session:
        referenced = {Path(p).resolve() for p in session.scalars(select(FileRecord.storage_path))}
    now = time.time() if now is None else now
    removed = []
    for path in sorted(upload_dir.iterdir()):
        try:
            if path.is_symlink() or not path.is_file() or path.resolve() in referenced:
                continue
            if now - path.stat().st_mtime < min_age_s:
                continue  # may be an upload whose record is not committed yet
            path.unlink()
            removed.append(path)
        except OSError:
            logger.warning("could not remove orphan upload %s", path.name)
    if removed:
        logger.info("removed %d orphan upload(s)", len(removed))
    return removed
