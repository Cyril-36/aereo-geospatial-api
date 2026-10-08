"""Precomputed text used to search and sort features (schema version 3).

Values are NFKC-normalised and casefolded in Python, so search and name sorting are
Unicode-aware; SQLite's own lower()/LIKE only fold ASCII.
"""

import unicodedata
from typing import Any

NAME_KEYS = ("name", "Name", "NAME")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def display_name(properties: dict[str, Any], source_id: str | None, index: int) -> str:
    for key in NAME_KEYS:
        value = properties.get(key)
        if value is not None and str(value).strip():
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
