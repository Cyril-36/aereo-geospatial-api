"""Upload flows and the results overview, in a real browser."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import SAMPLES, upload_via_ui

pytestmark = pytest.mark.e2e
RESULTS_URL = re.compile(r"/files/[0-9a-f-]{36}$")


def test_shapefile_upload_opens_results(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_parcels.zip")
    expect(page_ok).to_have_url(RESULTS_URL)
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("sample_parcels.zip")
    expect(page_ok.locator("#outcome-line")).to_have_text(
        "Processing completed: 3 of 3 features measured. Nothing needs attention."
    )
    expect(page_ok.locator("#crs-summary")).to_contain_text("EPSG:32643")
    expect(page_ok.locator("#crs-summary")).to_contain_text("from the file’s .prj")
    expect(page_ok.locator("#area-sum")).to_contain_text("52,738.94 m²")
    expect(page_ok.locator("#area-sum")).to_contain_text("Sum of feature areas")
    expect(page_ok.locator("#file-id")).to_have_text(re.compile(r"[0-9a-f-]{36}"))
    expect(page_ok.locator("#toast")).to_have_text(
        "Processing completed. Results are saved in file history."
    )


def test_kml_upload_survives_refresh(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    expect(page_ok.locator("#outcome-line")).to_have_text(
        "Processing completed: 2 of 3 features measured, 1 point with nothing to measure. "
        "Nothing needs attention."
    )
    expect(page_ok.locator("#crs-summary")).to_contain_text("KML always uses WGS 84")
    expect(page_ok.locator("#length-sum")).to_contain_text("Sum of feature lengths")
    page_ok.reload()
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("sample_survey.kml")


def test_invalid_geometry_outcome_counts_attention(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_invalid_geometry.kml")
    expect(page_ok.locator("#outcome-line")).to_have_text(
        "Processing completed: 1 of 5 features measured, 1 point with nothing to measure, "
        "3 need attention."
    )


def test_shows_configured_limit(page_ok, server):
    page_ok.goto(server.base_url + "/")
    expect(page_ok.locator("#limit-note")).to_contain_text("10 MiB")


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("site.kmz", b"PK", "KMZ isn’t supported yet"),
        ("parcels.geojson", b"{}", "This file type isn’t supported"),
        ("empty.kml", b"", "This file is empty"),
    ],
)
def test_client_side_rejections(page_ok, server, tmp_path, name, data, message):
    path = tmp_path / name
    path.write_bytes(data)
    page_ok.goto(server.base_url + "/")
    page_ok.set_input_files("#file-input", str(path))
    expect(page_ok.locator("#file-error")).to_contain_text(message)
    expect(page_ok.locator("#submit-upload")).to_be_disabled()


@pytest.mark.parametrize("server_env", [{"AEREO_MAX_UPLOAD_BYTES": "2048"}])
def test_size_limit_comes_from_the_server(page_ok, server):
    page_ok.goto(server.base_url + "/")
    expect(page_ok.locator("#limit-note")).to_contain_text("2.0 KB")
    page_ok.set_input_files("#file-input", str(SAMPLES / "sample_many_parcels.zip"))
    expect(page_ok.locator("#file-error")).to_contain_text("The limit is 2.0 KB")


def test_failed_upload_shows_guidance_and_record(page_ok, server, tmp_path):
    raw = bytearray((SAMPLES / "sample_parcels.zip").read_bytes())
    raw[200:260] = b"\x00" * 60  # corrupt the first entry's compressed data
    bad = tmp_path / "parcels_damaged.zip"
    bad.write_bytes(bytes(raw))
    upload_via_ui(page_ok, server, bad)
    outcome = page_ok.locator("#upload-outcome")
    expect(outcome).to_contain_text("The archive couldn’t be read")
    expect(outcome).to_contain_text("INVALID_ZIP")
    page_ok.get_by_role("button", name="Open the failed record").click()
    expect(page_ok.locator("#fail-panel")).to_contain_text("INVALID_ZIP")
    expect(page_ok.locator("#feature-table")).to_have_count(0)


def test_crs_conflict_offers_clearing(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_parcels.zip", crs="EPSG:4326")
    expect(page_ok.locator("#upload-outcome")).to_contain_text("doesn’t match the file")
    page_ok.get_by_role("button", name="Clear the coordinate system").click()
    expect(page_ok.locator("#crs-input")).to_have_value("")
    expect(page_ok.locator("#file-name")).to_have_text("sample_parcels.zip")


def test_crs_field_hint(page_ok, server):
    page_ok.goto(server.base_url + "/")
    page_ok.set_input_files("#file-input", str(SAMPLES / "sample_parcels.zip"))
    page_ok.fill("#crs-input", "utm 43")
    expect(page_ok.locator("#crs-hint")).to_contain_text("EPSG:32643")
    expect(page_ok.locator("#submit-upload")).to_be_disabled()


def test_missing_crs_then_reupload_with_crs(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_missing_crs.zip")
    expect(page_ok.locator("#outcome-line")).to_contain_text("nothing could be measured")
    page_ok.get_by_role("button", name="Upload again with a coordinate system").click()
    expect(page_ok.locator("#file-name")).to_have_text("sample_missing_crs.zip")
    page_ok.fill("#crs-input", "EPSG:32643")
    page_ok.click("#submit-upload")
    expect(page_ok.locator("#outcome-line")).to_contain_text("3 of 3 features measured")
    expect(page_ok.locator("#crs-summary")).to_contain_text("supplied at upload")


def test_double_click_creates_one_record(page_ok, server):
    page_ok.goto(server.base_url + "/")
    page_ok.set_input_files("#file-input", str(SAMPLES / "sample_survey.kml"))
    page_ok.locator("#submit-upload").dblclick()
    expect(page_ok).to_have_url(RESULTS_URL)
    files = page_ok.request.get(server.base_url + "/api/files/").json()["files"]
    assert len(files) == 1


def test_uncertain_outcome_on_proxy_5xx(page_ok, server):
    def gateway(route):
        if route.request.method == "POST":
            route.fulfill(status=502, body="Bad Gateway", content_type="text/plain")
        else:
            route.continue_()

    page_ok.route("**/api/files/", gateway)
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    expect(page_ok.locator("#upload-outcome")).to_contain_text("outcome is uncertain")
    expect(page_ok.get_by_role("button", name="Check file history")).to_be_visible()
    expect(page_ok.get_by_role("button", name="Upload again anyway")).to_be_visible()
    expect(page_ok.locator("#file-name")).to_have_text("sample_survey.kml")


def test_uncertain_outcome_on_dropped_connection(page_ok, server):
    page_ok.route(
        "**/api/files/",
        lambda r: r.abort("connectionreset") if r.request.method == "POST" else r.continue_(),
    )
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    expect(page_ok.locator("#upload-outcome")).to_contain_text("outcome is uncertain")


def test_server_unavailable_before_upload(page_ok, server):
    page_ok.route("**/api/config/", lambda r: r.abort("connectionrefused"))
    page_ok.goto(server.base_url + "/")
    expect(page_ok.locator("#config-error")).to_contain_text("The server isn’t responding")
    page_ok.unroute("**/api/config/")
    page_ok.get_by_role("button", name="Try again").click()
    expect(page_ok.locator("#limit-note")).to_contain_text("10 MiB")


def test_unknown_file_id(page_ok, server):
    page_ok.goto(server.base_url + "/files/00000000-0000-4000-8000-000000000000")
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("No file with this ID")
    page_ok.get_by_role("link", name="Go to file history").click()
    expect(page_ok).to_have_url(re.compile(r"/history$"))


def test_navigation_focuses_heading_and_marks_nav(page_ok, server):
    page_ok.goto(server.base_url + "/")
    page_ok.get_by_role("link", name="File history").click()
    expect(page_ok.get_by_role("heading", level=1)).to_be_focused()
    expect(page_ok.get_by_role("link", name="File history")).to_have_attribute(
        "aria-current", "page"
    )
    page_ok.go_back()
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("Measure a survey file")
