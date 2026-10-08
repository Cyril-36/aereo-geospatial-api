"""The map preview: what is drawn, what is skipped and why, limits, and selection sync."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import SAMPLES, upload_via_ui

pytestmark = pytest.mark.e2e
STATUS = "#map-status"


def opened(page, server, name):
    upload_via_ui(page, server, SAMPLES / name)
    expect(page).to_have_url(re.compile(r"/files/"))


def drawn(page):
    return page.evaluate("window.__aereoMap.drawn()")


def test_map_draws_supported_features(page_ok, server):
    opened(page_ok, server, "sample_invalid_geometry.kml")
    expect(page_ok.locator(STATUS)).to_have_text("5 of 5 matching features on the map.")
    assert drawn(page_ok) == [0, 1, 2, 3, 4]


def test_unknown_crs_is_never_plotted(page_ok, server):
    opened(page_ok, server, "sample_missing_crs.zip")
    expect(page_ok.locator(STATUS)).to_contain_text("No feature can be placed on the map")
    page_ok.locator("#map-skips summary").click()
    expect(page_ok.locator("#map-skips")).to_contain_text("coordinate system unknown")
    expect(page_ok.locator("#map-skips")).to_contain_text("P-001")
    assert drawn(page_ok) == []


def test_projected_source_geometry_is_not_plotted(page_ok, server, tmp_path):
    # A Shapefile in UTM whose features are measured is drawn from the transformed copy.
    opened(page_ok, server, "sample_parcels.zip")
    expect(page_ok.locator(STATUS)).to_have_text("3 of 3 matching features on the map.")


def test_map_follows_table_filters(page_ok, server):
    opened(page_ok, server, "sample_invalid_geometry.kml")
    page_ok.select_option("#f-type", "polygon")
    expect(page_ok.locator(STATUS)).to_have_text("3 of 3 matching features on the map.")
    assert drawn(page_ok) == [0, 1, 2]


def test_table_to_map_and_map_to_table(page_ok, server):
    opened(page_ok, server, "sample_many_parcels.zip")
    expect(page_ok.locator(STATUS)).to_contain_text("250 of 250")
    page_ok.locator("#feature-table tbody tr").nth(3).get_by_role("button").click()
    expect(page_ok).to_have_url(re.compile(r"feature=3"))
    assert page_ok.evaluate("window.__aereoMap.selected()") == 3
    page_ok.evaluate("window.__aereoMap.pick(240)")
    expect(page_ok.locator("#page-text")).to_have_text("Page 3 of 3")
    expect(page_ok.locator('#feature-table tr[aria-selected="true"]')).to_contain_text("M-241")
    expect(page_ok.locator("#details")).to_contain_text("M-241")
    assert page_ok.evaluate("window.__aereoMap.selected()") == 240


def test_fit_and_reset(page_ok, server):
    opened(page_ok, server, "sample_many_parcels.zip")
    expect(page_ok.locator(STATUS)).to_contain_text("250 of 250")
    page_ok.locator("#feature-table tbody tr").first.get_by_role("button").click()
    page_ok.get_by_role("button", name="Fit to all features").click()
    expect(page_ok.get_by_role("button", name="Fit to all features")).to_be_enabled()


def test_map_failure_leaves_table_usable(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_parcels.zip")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    page_ok.route(
        re.compile(r".*/measurements/.*limit=1000.*"), lambda r: r.abort("connectionreset")
    )
    page_ok.reload()
    expect(page_ok.locator("#map-region")).to_contain_text("The map preview couldn’t load")
    expect(page_ok.locator("#feature-table tbody tr")).to_have_count(3)
    page_ok.unroute(re.compile(r".*/measurements/.*limit=1000.*"))
    page_ok.get_by_role("button", name="Try loading the map again").click()
    expect(page_ok.locator(STATUS)).to_have_text("3 of 3 matching features on the map.")


@pytest.mark.parametrize("server_env", [{"AEREO_MAP_MAX_FEATURES": "100"}])
def test_partial_preview_and_load_more(page_ok, server):
    opened(page_ok, server, "sample_many_parcels.zip")
    expect(page_ok.locator(STATUS)).to_contain_text(
        "Map preview is partial: showing 100 of 250 matching features"
    )
    page_ok.get_by_role("button", name="Load more").click()
    expect(page_ok.locator(STATUS)).to_contain_text("showing 200 of 250")
    expect(page_ok.get_by_role("button", name="Load more")).to_have_count(0)  # capped at 2x


@pytest.mark.parametrize("server_env", [{"AEREO_MAP_MAX_VERTICES": "50"}])
def test_vertex_limit(page_ok, server):
    opened(page_ok, server, "sample_many_parcels.zip")
    expect(page_ok.locator(STATUS)).to_contain_text("showing 10 of 250 matching features")


def test_switching_files_cancels_map_load(page_ok, server):
    opened(page_ok, server, "sample_many_parcels.zip")
    first = page_ok.url
    opened(page_ok, server, "sample_parcels.zip")
    expect(page_ok.locator(STATUS)).to_have_text("3 of 3 matching features on the map.")
    page_ok.go_back()  # upload page
    page_ok.go_back()  # first results
    expect(page_ok).to_have_url(first)
    expect(page_ok.locator(STATUS)).to_contain_text("250 of 250")
    page_ok.go_forward()
    page_ok.go_forward()
    expect(page_ok.locator(STATUS)).to_have_text("3 of 3 matching features on the map.")
    assert drawn(page_ok) == [0, 1, 2]


def test_navigation_during_queued_canvas_redraw(page_ok, server):
    opened(page_ok, server, "sample_parcels.zip")
    page_ok.get_by_role("navigation", name="Main").get_by_role("link", name="File history").click()
    # Capture the real renderer when results reopen. Leaflet's update event can draw
    # synchronously while an earlier animation-frame redraw is still queued.
    page_ok.evaluate("""() => {
        const onAdd = L.Canvas.prototype.onAdd;
        L.Canvas.prototype.onAdd = function(map) {
            window.testCanvas = this;
            return onAdd.call(this, map);
        };
    }""")
    page_ok.get_by_role("button", name="sample_parcels.zip", exact=True).click()
    expect(page_ok.locator(STATUS)).to_contain_text("3 of 3")
    page_ok.evaluate("""() => {
        window.testCanvas.fire('update');
        document.querySelector('[data-nav="history"]').click();
    }""")
    page_ok.evaluate(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("File history")
    assert page_ok.expected_errors == []


@pytest.mark.parametrize("server_env", [{"AEREO_MAP_MAX_FEATURES": "1"}])
def test_unknown_coordinates_also_consume_loading_budget(page_ok, server):
    requests = []
    page_ok.on("request", lambda request: requests.append(request.url))
    opened(page_ok, server, "sample_missing_crs.zip")
    expect(page_ok.locator(STATUS)).to_contain_text("showing 0 of 3 matching features")
    assert drawn(page_ok) == []
    assert any("limit=1&offset=0" in url for url in requests)
    assert not any("limit=1000" in url for url in requests)
    page_ok.get_by_role("button", name="Load more").click()
    expect(page_ok.locator("#map-skips summary")).to_contain_text("2 not shown")


def test_pending_map_selection_cannot_change_another_view(page_ok, server):
    opened(page_ok, server, "sample_many_parcels.zip")
    expect(page_ok.locator(STATUS)).to_contain_text("250 of 250")
    pending = []
    page_ok.route("**/features/240/position/**", lambda route: pending.append(route))
    with page_ok.expect_request("**/features/240/position/**"):
        page_ok.evaluate("void window.__aereoMap.pick(240)")
    page_ok.get_by_role("navigation", name="Main").get_by_role("link", name="File history").click()
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("File history")
    assert pending
    pending[0].fulfill(
        json={"index": 240, "matches": True, "position": 240, "page_offset": 200, "limit": 100}
    )
    page_ok.wait_for_timeout(200)
    expect(page_ok).to_have_url(server.base_url + "/history")
