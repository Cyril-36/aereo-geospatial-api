"""The dataset-wide feature table and the feature details panel."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import SAMPLES, upload_via_ui

pytestmark = pytest.mark.e2e
ROWS = "#feature-table tbody tr"


def open_sample(page, server, name):
    upload_via_ui(page, server, SAMPLES / name)
    expect(page).to_have_url(re.compile(r"/files/"))
    expect(page.locator(ROWS).first).to_be_visible()


def test_invalid_geometry_reasons_visible(page_ok, server):
    open_sample(page_ok, server, "sample_invalid_geometry.kml")
    rows = page_ok.locator(ROWS)
    expect(rows).to_have_count(5)
    expect(rows.nth(0)).to_contain_text("48,012.35")
    expect(rows.nth(1)).to_contain_text("Invalid geometry")
    expect(rows.nth(1)).to_contain_text("Not measured")
    expect(rows.nth(3)).to_contain_text("1 warning")
    expect(rows.nth(4)).to_contain_text("n/a")
    expect(rows.nth(4)).not_to_contain_text("0.00")
    rows.nth(0).get_by_role("button").click()
    expect(page_ok.locator("#details")).not_to_contain_text("null")
    rows.nth(3).get_by_role("button").click()
    expect(page_ok.locator("#details")).to_contain_text("MULTIPLE_GEOMETRIES")
    expect(page_ok.locator("#details")).not_to_contain_text("[object")
    rows.nth(1).get_by_role("button").click()
    details = page_ok.locator("#details")
    expect(details).to_contain_text("RING_NOT_CLOSED")
    expect(details).to_contain_text("isn’t closed")
    expect(details).to_contain_text("Not measured")


def test_search_spans_all_pages(page_ok, server):
    open_sample(page_ok, server, "sample_many_parcels.zip")
    expect(page_ok.locator("#match-text")).to_have_text("All 250 features")
    expect(page_ok.locator("#page-text")).to_have_text("Page 1 of 3")
    page_ok.fill("#f-q", "M-249")
    expect(page_ok.locator("#match-text")).to_have_text("1 of 250 features match")
    expect(page_ok.locator(ROWS)).to_have_count(1)
    page_ok.get_by_role("button", name="Reset filters").first.click()
    expect(page_ok.locator("#match-text")).to_have_text("All 250 features")
    expect(page_ok.locator("#f-q")).to_have_value("")


def test_filters_match_nothing_then_reset(page_ok, server):
    open_sample(page_ok, server, "sample_invalid_geometry.kml")
    page_ok.select_option("#f-type", "line")
    expect(page_ok.locator("#table-empty")).to_contain_text("No features match these filters")
    page_ok.locator("#table-empty").get_by_role("button", name="Reset filters").click()
    expect(page_ok.locator(ROWS)).to_have_count(5)


def test_status_and_warning_filters(page_ok, server):
    open_sample(page_ok, server, "sample_invalid_geometry.kml")
    page_ok.select_option("#f-status", "attention")
    expect(page_ok.locator("#match-text")).to_have_text("3 of 5 features match")
    page_ok.select_option("#f-status", label="All statuses")
    page_ok.select_option("#f-warn", "with")
    expect(page_ok.locator(ROWS)).to_have_count(1)
    expect(page_ok.locator(ROWS).first).to_contain_text("Pump house")


def test_sort_and_page_survive_refresh(page_ok, server):
    open_sample(page_ok, server, "sample_many_parcels.zip")
    page_ok.select_option("#f-sort", "name")
    page_ok.click("#sort-dir")
    expect(page_ok.locator(ROWS).first).to_contain_text("M-250")
    page_ok.click("#next-page")
    expect(page_ok.locator("#page-text")).to_have_text("Page 2 of 3")
    expect(page_ok).to_have_url(re.compile(r"sort=name"))
    expect(page_ok).to_have_url(re.compile(r"order=desc"))
    expect(page_ok).to_have_url(re.compile(r"page=2"))
    first = page_ok.locator(ROWS).first.text_content()
    page_ok.reload()
    expect(page_ok.locator(ROWS).first).to_have_text(first)
    expect(page_ok.locator("#f-sort")).to_have_value("name")
    expect(page_ok.locator("#page-text")).to_have_text("Page 2 of 3")


def test_units_toggle_keeps_full_precision_in_details(page_ok, server):
    open_sample(page_ok, server, "sample_parcels.zip")
    page_ok.get_by_role("button", name="ha", exact=True).click()
    expect(page_ok.locator(ROWS).first).to_contain_text("0.9988")
    expect(page_ok.locator("#feature-table thead")).to_contain_text("Area (ha)")
    page_ok.locator(ROWS).first.get_by_role("button").click()
    expect(page_ok.locator("#details")).to_contain_text("0.9988 ha")
    fid = page_ok.url.split("/files/", 1)[1].split("?", 1)[0]
    original = page_ok.request.get(f"{server.base_url}/api/files/{fid}/measurements/").json()
    expect(page_ok.locator("#details")).to_contain_text(f"{original['features'][0]['area_m2']} m²")
    page_ok.reload()
    expect(page_ok.locator(ROWS).first).to_contain_text("0.9988")


def test_details_show_attributes_folder_and_technical(page_ok, server):
    open_sample(page_ok, server, "sample_survey.kml")
    page_ok.locator(ROWS).first.get_by_role("button").click()
    details = page_ok.locator("#details")
    expect(details).to_contain_text("ragi")
    expect(details).to_contain_text("Block A")
    expect(details.locator("details.tech")).not_to_have_attribute("open", "")
    details.locator("summary").click()
    expect(details).to_contain_text("LOCAL_LAEA")
    expect(details).to_contain_text("+proj=laea")


def test_details_escape_closes_and_returns_focus(page_ok, server):
    open_sample(page_ok, server, "sample_parcels.zip")
    button = page_ok.locator(ROWS).first.get_by_role("button")
    button.click()
    expect(page_ok.locator("#details")).to_be_visible()
    page_ok.keyboard.press("Escape")
    expect(page_ok.locator("#details")).to_be_hidden()
    expect(page_ok.locator(ROWS).first.get_by_role("button")).to_be_focused()
    expect(page_ok).not_to_have_url(re.compile(r"feature="))


def test_selection_survives_refresh(page_ok, server):
    open_sample(page_ok, server, "sample_many_parcels.zip")
    page_ok.click("#next-page")
    page_ok.locator(ROWS).nth(2).get_by_role("button").click()
    expect(page_ok).to_have_url(re.compile(r"feature=102"))
    page_ok.reload()
    expect(page_ok.locator("#details")).to_contain_text("M-103")
    expect(page_ok.locator('#feature-table tr[aria-selected="true"]')).to_contain_text("M-103")


def test_selected_feature_hidden_by_filters(page_ok, server):
    open_sample(page_ok, server, "sample_invalid_geometry.kml")
    page_ok.locator(ROWS).nth(0).get_by_role("button").click()
    page_ok.select_option("#f-status", "attention")
    expect(page_ok.locator("#sel-hidden")).to_contain_text("hidden by the current filters")
    page_ok.locator("#sel-hidden").get_by_role("button", name="Reset filters").click()
    expect(page_ok.locator('#feature-table tr[aria-selected="true"]')).to_contain_text(
        "Valid field"
    )


def test_stale_filter_responses_are_dropped(page_ok, server):
    open_sample(page_ok, server, "sample_many_parcels.zip")

    def slow(route):
        if "q=M-1&" in route.request.url or route.request.url.endswith("q=M-1"):
            page_ok.wait_for_timeout(1500)
        route.continue_()

    page_ok.route("**/measurements/**", slow)
    page_ok.fill("#f-q", "M-1")
    page_ok.wait_for_timeout(400)  # past the debounce: the slow request is in flight
    page_ok.fill("#f-q", "M-123")
    expect(page_ok.locator(ROWS)).to_have_count(1)
    page_ok.wait_for_timeout(2000)
    expect(page_ok.locator(ROWS)).to_have_count(1)
    expect(page_ok.locator(ROWS).first).to_contain_text("M-123")
    expect(page_ok.locator("#f-q")).to_have_value("M-123")


def test_table_load_failure_is_contained(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_parcels.zip")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    page_ok.route("**/measurements/**", lambda r: r.abort("connectionreset"))
    page_ok.reload()
    expect(page_ok.locator("#table-region")).to_contain_text("Couldn’t load features")
    expect(page_ok.locator("#outcome-line")).to_contain_text("3 of 3 features measured")
    page_ok.unroute("**/measurements/**")
    page_ok.locator("#table-region").get_by_role("button", name="Try again").click()
    expect(page_ok.locator(ROWS)).to_have_count(3)


def test_hostile_names_render_as_text(page_ok, server, tmp_path):
    path = tmp_path / "evil.kml"
    path.write_text(
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><name>&lt;img src=x onerror=alert(1)&gt;</name>"
        '<ExtendedData><Data name="&lt;b&gt;k"><value>&lt;script&gt;v</value></Data>'
        "</ExtendedData><Point><coordinates>77.59,12.97</coordinates></Point></Placemark>"
        "</Document></kml>"
    )
    upload_via_ui(page_ok, server, path)
    expect(page_ok.locator(ROWS).first).to_contain_text("<img src=x onerror=alert(1)>")
    page_ok.locator(ROWS).first.get_by_role("button").click()
    expect(page_ok.locator("#details")).to_contain_text("<script>v")
    assert page_ok.locator("#app img, #app script, #app b").count() == 0
