"""Precomputed text used to search and sort features (schema version 3).

Values are NFKC-normalised and casefolded in Python, so search and name sorting are
Unicode-aware; SQLite's own lower()/LIKE only fold ASCII.
"""

import unicodedata
from typing import Any

NAME_KEYS = ("name", "title", "label")  # matched case-insensitively, in this order


def _is_id_key(key: str) -> bool:
    k = key.casefold()
    return k == "id" or k.endswith(("_id", "-id"))


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _present(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def display_name(properties: dict[str, Any], source_id: str | None, index: int) -> str:
    """A name-like attribute, else an ID-like one (``id``, ``*_id``), else the source ID."""
    folded = {key.casefold(): value for key, value in reversed(list(properties.items()))}
    for key in NAME_KEYS:
        if _present(folded.get(key)):
            return str(folded[key]).strip()
    for key, value in properties.items():
        if _is_id_key(key) and _present(value):
            return str(value).strip()
    if source_id is not None and str(source_id).strip():
        return str(source_id)
    return f"Feature {index}"


def search_text(name: str, source_id: str | None, properties: dict[str, Any]) -> str:
    lines = [name, source_id or ""]
    lines += [f"{key} {'' if value is None else value}" for key, value in properties.items()]
    return normalize("\n".join(lines))


def fill_search_columns(feature) -> None:
    """Set a FeatureRecord's display_name, sort_name, search_text and warning_count."""
    properties = feature.properties or {}
    name = display_name(properties, feature.source_id, feature.index)
    feature.display_name = name
    feature.sort_name = normalize(name)
    feature.search_text = search_text(name, feature.source_id, properties)
    feature.warning_count = len(feature.warnings or [])
