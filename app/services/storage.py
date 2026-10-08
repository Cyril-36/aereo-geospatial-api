"""Streamed, size-limited storage of uploads under random names."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

from app.errors import IngestionError
from app.services.dataset import FileFormat

CHUNK_SIZE = 1024 * 1024

EXTENSIONS: dict[str, FileFormat] = {".zip": "SHAPEFILE", ".kml": "KML"}


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    size_bytes: int
    sha256: str


def display_filename(filename: str | None) -> str:
    """The client's filename reduced to its last path component; used for display only."""
    name = PureWindowsPath(PurePosixPath(filename or "").name).name.strip()
    return name[:255] or "upload"


def classify_filename(filename: str | None) -> FileFormat:
    """Map the declared extension to a format. Content is validated later by the readers."""
    suffix = PurePosixPath(display_filename(filename)).suffix.lower()
    if suffix == ".kmz":
        raise IngestionError(
            "KMZ_UNSUPPORTED",
            "KMZ is not supported yet. Unzip it and upload the .kml file inside.",
        )
    if suffix not in EXTENSIONS:
        raise IngestionError(
            "UNSUPPORTED_FORMAT",
            "Upload a .zip containing one Shapefile or a .kml file.",
        )
    return EXTENSIONS[suffix]


def save_stream(source: BinaryIO, destination: Path, max_bytes: int) -> StoredUpload:
    """Copy ``source`` to ``destination`` counting the bytes actually read.

    Fails with UPLOAD_TOO_LARGE as soon as ``max_bytes`` is exceeded and with EMPTY_FILE for
    zero bytes; in both cases nothing is left on disk.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with partial.open("xb") as out:
            while chunk := source.read(CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise IngestionError(
                        "UPLOAD_TOO_LARGE", f"File exceeds the {max_bytes}-byte upload limit."
                    )
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise IngestionError("EMPTY_FILE", "The uploaded file is empty.")
        os.replace(partial, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return StoredUpload(destination, size, digest.hexdigest())
