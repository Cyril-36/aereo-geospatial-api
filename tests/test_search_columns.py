"""Precomputed feature search and sort keys (schema version 3)."""

from pathlib import Path

from sqlalchemy import select

from app.models import FeatureRecord
from app.services.search import display_name, normalize, search_text

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def test_display_name_precedence():
    assert display_name({"name": "Field 1", "Name": "x"}, "f1", 0) == "Field 1"
    assert display_name({"Name": "Plot"}, "f1", 0) == "Plot"
    assert display_name({"name": "  "}, "f1", 3) == "f1"
    assert display_name({}, None, 3) == "Feature 3"
    assert display_name({}, "  ", 4) == "Feature 4"
    assert display_name({"name": 17}, None, 0) == "17"
    assert display_name({"TITLE": "Plot 4"}, "f1", 0) == "Plot 4"
    assert display_name({"land_use": "x", "parcel_id": "P-001"}, "0", 0) == "P-001"
    assert display_name({"ID": 7, "label": "North"}, "0", 0) == "North"
    assert display_name({"valid": "yes", "paid": "no"}, "3", 3) == "3"  # not id-like keys


def test_normalize_is_unicode_aware():
    assert normalize("ＳＴＲＡẞE") == normalize("strasse")
    assert normalize("ಬೆಂಗಳೂರು") == "ಬೆಂಗಳೂರು"
    assert normalize("Ünïcode") == "ünïcode"


def test_search_text_holds_name_id_keys_and_values():
    text = search_text("Field 1", "f-1", {"Owner": "Rao", "crop": None, "n": 5})
    for needle in ("field 1", "f-1", "owner rao", "crop", "n 5"):
        assert needle in text


def test_columns_are_filled_when_results_are_stored(client):
    data = (SAMPLES / "sample_invalid_geometry.kml").read_bytes()
    file_id = client.post("/api/files/", files={"file": ("s.kml", data)}).json()["id"]
    with client.app.state.session_factory() as session:
        rows = session.scalars(
            select(FeatureRecord)
            .where(FeatureRecord.file_id == file_id)
            .order_by(FeatureRecord.index)
        ).all()
        got = [(r.display_name, r.sort_name, r.warning_count) for r in rows]
        assert "bow-tie boundary" in rows[2].search_text
    assert got == [
        ("Valid field", "valid field", 0),
        ("Unclosed plot", "unclosed plot", 0),
        ("Bow-tie boundary", "bow-tie boundary", 0),
        ("Pump house", "pump house", 1),
        ("Borewell", "borewell", 0),
    ]
