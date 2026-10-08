import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import SAMPLES

pytestmark = pytest.mark.e2e


def test_keyboard_only_upload_to_export(page_ok, server):
    page_ok.goto(server.base_url + "/")
    page_ok.set_input_files("#file-input", str(SAMPLES / "sample_parcels.zip"))
    page_ok.focus("#crs-input")
    page_ok.keyboard.press("Tab")
    expect(page_ok.locator("#submit-upload")).to_be_focused()
    page_ok.keyboard.press("Enter")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    expect(page_ok.get_by_role("heading", level=1)).to_be_focused()
    page_ok.get_by_role("button", name="Download results").focus()
    page_ok.keyboard.press("Enter")
    expect(page_ok.get_by_role("dialog")).to_be_visible()
    page_ok.keyboard.press("Escape")
    expect(page_ok.get_by_role("button", name="Download results")).to_be_focused()
    page_ok.keyboard.press("Enter")
    page_ok.get_by_role("dialog").get_by_role("button", name="Download 3 features").focus()
    with page_ok.expect_download() as download:
        page_ok.keyboard.press("Enter")
    assert download.value.suggested_filename == "sample_parcels-all.csv"


def test_back_forward_restores_views_filters_and_selection(page_ok, server):
    page_ok.goto(server.base_url + "/")
    page_ok.set_input_files("#file-input", str(SAMPLES / "sample_many_parcels.zip"))
    page_ok.click("#submit-upload")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    page_ok.click("#next-page")
    page_ok.locator("#feature-table tbody tr").first.get_by_role("button").click()
    selected = page_ok.url
    page_ok.get_by_role("navigation", name="Main").get_by_role("link", name="File history").click()
    page_ok.go_back()
    expect(page_ok).to_have_url(selected)
    expect(page_ok.locator("#page-text")).to_have_text("Page 2 of 3")
    expect(page_ok.locator("#details")).to_be_visible()
    page_ok.go_back()
    expect(page_ok.locator("#details")).to_be_hidden()
    page_ok.go_forward()
    expect(page_ok.locator("#details")).to_be_visible()


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}])
def test_mobile_layout_has_no_horizontal_page_scroll(page_ok, server, viewport):
    page_ok.set_viewport_size(viewport)
    for path in ("/", "/history"):
        page_ok.goto(server.base_url + path)
        assert page_ok.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page_ok.goto(server.base_url + "/")
    page_ok.set_input_files("#file-input", str(SAMPLES / "sample_survey.kml"))
    page_ok.click("#submit-upload")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    assert page_ok.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page_ok.locator("#feature-table tbody tr").first.get_by_role("button").click()
    expect(page_ok.locator("#details")).to_be_in_viewport()


def test_every_input_is_labelled(page_ok, server):
    for path in ("/", "/history"):
        page_ok.goto(server.base_url + path)
        unlabeled = page_ok.evaluate("""() => [...document.querySelectorAll('input,select')]
          .filter(e => e.type !== 'hidden' && !e.labels?.length && !e.getAttribute('aria-label'))
          .map(e => e.id || e.name)""")
        assert unlabeled == []


def test_status_badges_use_text_and_dialog_focus_is_trapped(page_ok, server):
    from tests.e2e.conftest import upload_via_ui

    upload_via_ui(page_ok, server, SAMPLES / "sample_invalid_geometry.kml")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    expect(page_ok.locator("#feature-table tbody tr")).to_have_count(5)
    badges = page_ok.locator("#feature-table tbody .badge").all_text_contents()
    assert badges == [
        "Measured",
        "Invalid geometry",
        "Invalid geometry",
        "Unsupported geometry",
        "Not applicable",
    ]
    page_ok.get_by_role("button", name="Download results").click()
    for _ in range(12):
        page_ok.keyboard.press("Tab")
        assert page_ok.evaluate("document.activeElement.closest('dialog') !== null")
    page_ok.keyboard.press("Escape")
    expect(page_ok.get_by_role("button", name="Download results")).to_be_focused()
