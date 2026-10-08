import csv
import io
import json
import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import SAMPLES, upload_via_ui

pytestmark = pytest.mark.e2e


def test_history_persists_across_restart_and_reopens(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_parcels.zip")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    server.restart()
    page_ok.goto(server.base_url + "/history")
    rows = page_ok.locator("#history-table tbody tr")
    expect(rows).to_have_count(1)
    rows.first.get_by_role("button", name="sample_parcels.zip", exact=True).click()
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("sample_parcels.zip")


def test_history_search_and_status_filter(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    upload_via_ui(page_ok, server, SAMPLES / "sample_parcels.zip", crs="EPSG:4326")  # fails
    expect(page_ok.locator("#upload-outcome")).to_contain_text("CRS_CONFLICT")
    page_ok.goto(server.base_url + "/history")
    page_ok.select_option("#h-status", "FAILED")
    expect(page_ok.locator("#history-table tbody tr")).to_have_count(1)
    expect(page_ok.locator("#history-table")).to_contain_text("CRS_CONFLICT")
    page_ok.select_option("#h-status", "all")
    page_ok.fill("#h-q", "survey")
    expect(page_ok.locator("#history-table tbody tr")).to_have_count(1)


def test_delete_requires_confirmation(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    page_ok.goto(server.base_url + "/history")
    page_ok.get_by_role("button", name="Delete sample_survey.kml").click()
    dialog = page_ok.get_by_role("alertdialog")
    expect(dialog).to_contain_text("can’t be undone")
    dialog.get_by_role("button", name="Keep it").click()
    expect(page_ok.locator("#history-table tbody tr")).to_have_count(1)
    page_ok.get_by_role("button", name="Delete sample_survey.kml").click()
    page_ok.get_by_role("alertdialog").get_by_role("button", name="Delete file").click()
    expect(page_ok.locator("#history-empty")).to_contain_text("No files yet")


def test_deleted_file_in_open_tab(page_ok, server, context):
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    url = page_ok.url
    fid = url.rsplit("/", 1)[1].split("?")[0]
    assert page_ok.request.delete(f"{server.base_url}/api/files/{fid}/").status == 204
    page_ok.reload()
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("No file with this ID")


def test_deleted_file_is_detected_on_next_table_request(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    expect(page_ok).to_have_url(re.compile(r"/files/"))
    expect(page_ok.locator("#feature-table tbody tr")).to_have_count(3)
    fid = page_ok.url.rsplit("/", 1)[1].split("?")[0]
    assert page_ok.request.delete(f"{server.base_url}/api/files/{fid}/").status == 204
    page_ok.select_option("#f-status", "attention")
    expect(page_ok.get_by_role("heading", level=1)).to_have_text("No file with this ID")


def test_filtered_export_is_complete(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_many_parcels.zip")
    page_ok.select_option("#f-sort", "name")
    page_ok.fill("#f-q", "industrial")
    expect(page_ok.locator("#match-text")).to_have_text("83 of 250 features match")
    page_ok.get_by_role("button", name="Download results").click()
    dlg = page_ok.get_by_role("dialog")
    dlg.get_by_label(re.compile("Only features matching")).check()
    with page_ok.expect_download() as dl:
        dlg.get_by_role("button", name=re.compile("Download 83 features")).click()
    text = dl.value.path().read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 83
    assert dl.value.suggested_filename == "sample_many_parcels-filtered.csv"


def test_all_json_export(page_ok, server):
    upload_via_ui(page_ok, server, SAMPLES / "sample_many_parcels.zip")
    page_ok.get_by_role("button", name="Download results").click()
    dlg = page_ok.get_by_role("dialog")
    dlg.get_by_label(re.compile("JSON")).check()
    with page_ok.expect_download() as dl:
        dlg.get_by_role("button", name=re.compile("Download 250 features")).click()
    assert json.loads(dl.value.path().read_text())["export"]["feature_count"] == 250


def test_uncertain_upload_history_check(page_ok, server):
    page_ok.route(
        "**/api/files/", lambda r: r.abort() if r.request.method == "POST" else r.continue_()
    )
    upload_via_ui(page_ok, server, SAMPLES / "sample_survey.kml")
    page_ok.get_by_role("button", name="Check file history").click()
    expect(page_ok).to_have_url(re.compile(r"/history\?since="))
    expect(page_ok.locator("#history-since")).to_contain_text("sample_survey.kml")
