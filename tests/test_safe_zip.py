import io
import stat
import struct
import unicodedata
import zipfile
from pathlib import Path

import pytest

from app.errors import IngestionError
from app.services.dataset import IngestionLimits
from app.services.safe_zip import extract_shapefile
from tests.conftest import LIMITS, make_zip, write_shapefile


def run(tmp_path: Path, archive: bytes, limits: IngestionLimits = LIMITS):
    path = tmp_path / "upload.zip"
    path.write_bytes(archive)
    out = tmp_path / "out"
    return extract_shapefile(path, out, limits), out


def expect(code: str, tmp_path: Path, archive: bytes, limits: IngestionLimits = LIMITS):
    with pytest.raises(IngestionError) as exc_info:
        run(tmp_path, archive, limits)
    assert exc_info.value.code == code
    return exc_info.value


@pytest.fixture
def parts(tmp_path: Path) -> dict[str, bytes]:
    return write_shapefile(tmp_path / "src")


def test_extracts_only_dataset_components_under_generated_names(tmp_path, parts):
    archive = make_zip([*parts.items(), ("readme.txt", b"hello")])
    components, out = run(tmp_path, archive)
    assert sorted(p.name for p in out.iterdir()) == [
        "dataset.cpg",
        "dataset.dbf",
        "dataset.prj",
        "dataset.shp",
        "dataset.shx",
    ]
    assert components.shp.read_bytes() == parts["plots.shp"]
    assert components.archive_name == "plots.shp"


def test_nested_folder_and_uppercase_extensions_are_accepted(tmp_path, parts):
    archive = make_zip((f"survey/2026/PLOTS{Path(n).suffix.upper()}", d) for n, d in parts.items())
    components, _ = run(tmp_path, archive)
    assert components.archive_name == "survey/2026/PLOTS.SHP"
    assert components.prj is not None


def test_missing_prj_is_allowed(tmp_path, parts):
    archive = make_zip((n, d) for n, d in parts.items() if not n.endswith(".prj"))
    components, _ = run(tmp_path, archive)
    assert components.prj is None


def test_macos_metadata_entries_are_ignored(tmp_path, parts):
    junk = [("__MACOSX/._plots.shp", b"\x00\x05\x16\x07"), ("._plots.shp", b"\x00")]
    components, _ = run(tmp_path, make_zip([*parts.items(), *junk]))
    assert components.archive_name == "plots.shp"


@pytest.mark.parametrize("missing", [".shx", ".dbf"])
def test_missing_mandatory_component(tmp_path, parts, missing):
    archive = make_zip((n, d) for n, d in parts.items() if not n.endswith(missing))
    err = expect("MISSING_SHAPEFILE_COMPONENT", tmp_path, archive)
    assert missing in err.message


def test_components_in_different_folders_do_not_pair(tmp_path, parts):
    archive = make_zip((("other/" if n.endswith(".dbf") else "") + n, d) for n, d in parts.items())
    expect("MISSING_SHAPEFILE_COMPONENT", tmp_path, archive)


def test_multiple_datasets_rejected_not_first_chosen(tmp_path, parts):
    second = {n.replace("plots", "roads"): d for n, d in parts.items()}
    err = expect(
        "MULTIPLE_DATASETS_UNSUPPORTED", tmp_path, make_zip([*parts.items(), *second.items()])
    )
    assert "plots.shp" in err.message and "roads.shp" in err.message


def test_no_shapefile_hints_at_kml(tmp_path):
    err = expect("NO_SHAPEFILE", tmp_path, make_zip([("doc.kml", b"<kml/>")]))
    assert ".kml" in err.message


@pytest.mark.parametrize(
    "name",
    ["../plots.shp", "a/../../plots.shp", "/etc/plots.shp", "C:\\temp\\plots.shp", "..\\plots.shp"],
)
def test_path_traversal_and_absolute_paths_rejected(tmp_path, parts, name):
    archive = make_zip([*parts.items(), (name, b"x")])
    expect("UNSAFE_ARCHIVE_PATH", tmp_path, archive)
    assert not (tmp_path / "plots.shp").exists()
    assert not (tmp_path / "out").exists()  # nothing written before validation finished


def test_symlink_entry_rejected(tmp_path, parts):
    link = zipfile.ZipInfo("link.dbf")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    expect("ARCHIVE_SYMLINK", tmp_path, make_zip([*parts.items(), (link, b"/etc/passwd")]))


def test_encrypted_entry_rejected(tmp_path, parts):
    # zipfile cannot write encrypted entries, so set the "encrypted" flag bit directly.
    archive = bytearray(make_zip([*parts.items(), ("notes.txt", b"x")]))
    central, local = _entry_offsets(archive, "notes.txt")
    archive[central + 8] |= 0x1
    archive[local + 6] |= 0x1
    expect("ENCRYPTED_ARCHIVE_ENTRY", tmp_path, bytes(archive))


def test_case_collision_rejected(tmp_path, parts):
    expect("DUPLICATE_ARCHIVE_PATH", tmp_path, make_zip([*parts.items(), ("PLOTS.dbf", b"x")]))


def test_unicode_normalisation_collision_rejected(tmp_path, parts):
    nfc = unicodedata.normalize("NFC", "café.txt")
    nfd = unicodedata.normalize("NFD", "café.txt")
    assert nfc != nfd
    expect("DUPLICATE_ARCHIVE_PATH", tmp_path, make_zip([*parts.items(), (nfc, b"1"), (nfd, b"2")]))


@pytest.mark.parametrize("name", ["inner.zip", "deep/x.KMZ", "a.tar.gz"])
def test_nested_archive_rejected(tmp_path, parts, name):
    expect("NESTED_ARCHIVE", tmp_path, make_zip([*parts.items(), (name, b"PK")]))


def test_too_many_members(tmp_path, parts):
    limits = IngestionLimits(10**8, 6, 100, 1000)
    extra = [(f"f{i}.txt", b"") for i in range(5)]
    expect("TOO_MANY_ARCHIVE_MEMBERS", tmp_path, make_zip([*parts.items(), *extra]), limits)


def test_zip_bomb_rejected_by_declared_size(tmp_path, parts):
    limits = IngestionLimits(1024 * 1024, 50, 100, 1000)
    bomb = b"\0" * (2 * 1024 * 1024)  # compresses to ~2 KB
    archive = make_zip([*parts.items(), ("padding.txt", bomb)])
    assert len(archive) < 20_000
    expect("EXPANDED_SIZE_EXCEEDED", tmp_path, archive, limits)


def _entry_offsets(archive: bytearray, name: str) -> tuple[int, int]:
    """Offsets of an entry's central-directory header and local header."""
    pos = archive.find(b"PK\x01\x02")
    while pos != -1:
        name_len = struct.unpack_from("<H", archive, pos + 28)[0]
        if archive[pos + 46 : pos + 46 + name_len] == name.encode():
            return pos, struct.unpack_from("<I", archive, pos + 42)[0]
        pos = archive.find(b"PK\x01\x02", pos + 4)
    raise AssertionError(f"{name} not found in central directory")


def _set_declared_size(archive: bytearray, name: str, size: int) -> None:
    central, local = _entry_offsets(archive, name)
    struct.pack_into("<I", archive, central + 24, size)
    struct.pack_into("<I", archive, local + 22, size)


def test_understated_size_cannot_write_past_the_budget(tmp_path, parts):
    """An archive lying about its uncompressed size must not write past the declared bytes."""
    big_dbf = parts["plots.dbf"] + b"\0" * 200_000
    entries = [(n, big_dbf if n.endswith(".dbf") else d) for n, d in parts.items()]
    archive = bytearray(make_zip(entries))
    _set_declared_size(archive, "plots.dbf", 10)

    limits = IngestionLimits(50_000, 50, 100, 1000)
    with pytest.raises(IngestionError) as exc_info:
        run(tmp_path, bytes(archive), limits)
    assert exc_info.value.code in {"INVALID_ZIP", "EXPANDED_SIZE_EXCEEDED"}
    written = sum(p.stat().st_size for p in (tmp_path / "out").glob("*"))
    assert written <= 50_000


def test_corrupt_zip(tmp_path):
    expect("INVALID_ZIP", tmp_path, b"PK\x03\x04 definitely not a zip")


def test_crc_mismatch_is_reported(tmp_path, parts):
    archive = bytearray(make_zip(parts.items()))
    with zipfile.ZipFile(io.BytesIO(bytes(archive))) as zf:
        info = zf.getinfo("plots.prj")
    data_start = info.header_offset + 30 + len(info.filename) + len(info.extra)
    archive[data_start + 2] ^= 0xFF  # corrupt compressed bytes
    expect("INVALID_ZIP", tmp_path, bytes(archive))
