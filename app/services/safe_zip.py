"""Bounded, traversal-safe inspection and extraction of a zipped Shapefile.

Every entry is validated before anything is written. Only the components of the single
Shapefile dataset are extracted, under fixed generated names, so no archive path is ever
used as a filesystem path. ``ZipFile.extractall`` is never used.
"""

import lzma
import posixpath
import re
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.errors import IngestionError
from app.services.dataset import IngestionLimits

COMPONENTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
REQUIRED = (".shp", ".shx", ".dbf")
ARCHIVE_SUFFIXES = {".zip", ".kmz", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z", ".rar"}
ENCRYPTED_FLAG = 0x1
CHUNK_SIZE = 1024 * 1024
_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ShapefileComponents:
    shp: Path
    shx: Path
    dbf: Path
    prj: Path | None
    cpg: Path | None
    archive_name: str  # dataset path inside the archive, for messages


def _is_macos_metadata(parts: tuple[str, ...]) -> bool:
    # Finder-made archives add __MACOSX/ and AppleDouble "._name" files; they are not data.
    return parts[0] == "__MACOSX" or parts[-1].startswith("._")


def _safe_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE.match(normalized):
        raise IngestionError("UNSAFE_ARCHIVE_PATH", f"Archive entry has an absolute path: {name!r}")
    parts = tuple(p for p in normalized.split("/") if p not in ("", "."))
    if any(p == ".." for p in parts) or not parts:
        raise IngestionError("UNSAFE_ARCHIVE_PATH", f"Archive entry escapes the archive: {name!r}")
    return parts


def _collision_key(parts: tuple[str, ...]) -> str:
    return unicodedata.normalize("NFC", "/".join(parts)).casefold()


def extract_shapefile(
    archive: Path, destination: Path, limits: IngestionLimits
) -> ShapefileComponents:
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise IngestionError("INVALID_ZIP", "The .zip file is not a valid ZIP archive.") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_archive_members:
            raise IngestionError(
                "TOO_MANY_ARCHIVE_MEMBERS",
                f"Archive has {len(infos)} entries; the limit is {limits.max_archive_members}.",
            )

        seen: set[str] = set()
        declared_total = 0
        # dataset key (directory + casefolded stem) -> {suffix: ZipInfo}
        datasets: dict[str, dict[str, zipfile.ZipInfo]] = {}
        dataset_names: dict[str, str] = {}

        for info in infos:
            parts = _safe_parts(info.filename)
            key = _collision_key(parts)
            if key in seen:
                raise IngestionError(
                    "DUPLICATE_ARCHIVE_PATH",
                    f"Archive contains colliding paths (case or Unicode form): {info.filename!r}",
                )
            seen.add(key)
            if info.is_dir():
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise IngestionError(
                    "ARCHIVE_SYMLINK", f"Archive entry is a symlink: {info.filename!r}"
                )
            if info.flag_bits & ENCRYPTED_FLAG:
                raise IngestionError(
                    "ENCRYPTED_ARCHIVE_ENTRY", f"Archive entry is encrypted: {info.filename!r}"
                )
            if _is_macos_metadata(parts):
                continue
            suffix = PurePosixPath(parts[-1]).suffix.lower()
            if suffix in ARCHIVE_SUFFIXES:
                raise IngestionError(
                    "NESTED_ARCHIVE", f"Nested archives are not unpacked: {info.filename!r}"
                )
            declared_total += info.file_size
            if declared_total > limits.max_expanded_bytes:
                raise IngestionError(
                    "EXPANDED_SIZE_EXCEEDED",
                    f"Archive expands beyond the {limits.max_expanded_bytes}-byte limit.",
                )
            if suffix in COMPONENTS:
                stem = posixpath.join(*parts[:-1], PurePosixPath(parts[-1]).stem)
                ds_key = unicodedata.normalize("NFC", stem).casefold()
                datasets.setdefault(ds_key, {})[suffix] = info
                if suffix == ".shp":
                    dataset_names[ds_key] = "/".join(parts)

        shp_keys = [k for k, comps in datasets.items() if ".shp" in comps]
        if not shp_keys:
            hint = (
                " (found a .kml: upload it directly)"
                if any(n.lower().endswith(".kml") for n in zf.namelist())
                else ""
            )
            raise IngestionError("NO_SHAPEFILE", f"The archive contains no .shp file{hint}.")
        if len(shp_keys) > 1:
            names = ", ".join(sorted(dataset_names[k] for k in shp_keys))
            raise IngestionError(
                "MULTIPLE_DATASETS_UNSUPPORTED",
                f"The archive must contain exactly one Shapefile; found {len(shp_keys)}: {names}",
            )

        key = shp_keys[0]
        components = datasets[key]
        missing = [s for s in REQUIRED if s not in components]
        if missing:
            raise IngestionError(
                "MISSING_SHAPEFILE_COMPONENT",
                f"Shapefile {dataset_names[key]!r} is missing {', '.join(missing)}.",
            )

        destination.mkdir(parents=True, exist_ok=True)
        too_big = IngestionError(
            "EXPANDED_SIZE_EXCEEDED",
            f"Archive expands beyond the {limits.max_expanded_bytes}-byte limit.",
        )
        written_total = 0
        paths: dict[str, Path] = {}
        for suffix, info in components.items():
            target = destination / f"dataset{suffix}"
            try:
                with zf.open(info) as src, target.open("xb") as out:
                    while chunk := src.read(CHUNK_SIZE):
                        written_total += len(chunk)
                        if written_total > limits.max_expanded_bytes:
                            raise too_big
                        out.write(chunk)
            # zlib/lzma errors: a corrupt compressed stream inside an intact archive.
            except (
                zipfile.BadZipFile,
                EOFError,
                OSError,
                NotImplementedError,
                zlib.error,
                lzma.LZMAError,
            ) as exc:
                raise IngestionError(
                    "INVALID_ZIP", f"Archive entry could not be read: {info.filename!r}"
                ) from exc
            paths[suffix] = target

        return ShapefileComponents(
            shp=paths[".shp"],
            shx=paths[".shx"],
            dbf=paths[".dbf"],
            prj=paths.get(".prj"),
            cpg=paths.get(".cpg"),
            archive_name=dataset_names[key],
        )
