"""Static checks on the workspace's client code: error guidance coverage and safe rendering."""

import re
from pathlib import Path

from app.errors import ERROR_STATUS

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
JS = WEB / "js"

# Codes the API can return in an error envelope, beyond the ingestion codes in ERROR_STATUS.
API_CODES = {
    "INTERRUPTED",
    "INTERNAL_ERROR",
    "INCONSISTENT_RESULT",
    "PERSISTENCE_FAILED",
    "FILE_NOT_FOUND",
    "FILE_FAILED",
    "FILE_NOT_READY",
    "FEATURE_NOT_FOUND",
    "MEASUREMENTS_UNAVAILABLE",
    "FORM_LIMIT_EXCEEDED",
    "MALFORMED_REQUEST",
    "DUPLICATE_FORM_FIELD",
    "VALIDATION_ERROR",
}


def guide_keys() -> set[str]:
    text = (JS / "format.js").read_text()
    block = text[text.index("export const ERROR_GUIDE") :]
    block = block[: block.index("\n};")]
    return set(re.findall(r"^  ([A-Z_]+): \{", block, re.M))


def test_every_backend_error_code_has_guidance():
    missing = (set(ERROR_STATUS) | API_CODES) - guide_keys()
    assert not missing, sorted(missing)


def test_no_html_parsing_sinks_in_client_code():
    sinks = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(")
    offenders = [
        f"{js.name}: {sink}" for js in JS.glob("*.js") for sink in sinks if sink in js.read_text()
    ]
    assert offenders == []


def test_client_code_makes_no_external_requests():
    pattern = re.compile(r"https?://")
    allowed = {"map.js"}  # basemap attribution link only; tile URL comes from /api/config/
    svg_namespace = "http://www.w3.org/2000/svg"  # an identifier, never fetched
    for js in JS.glob("*.js"):
        if js.name not in allowed:
            assert not pattern.search(js.read_text().replace(svg_namespace, "")), js.name
    assert not pattern.search((WEB / "index.html").read_text())
