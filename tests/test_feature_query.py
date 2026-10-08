"""Opt-in dataset-wide filtering and sorting of measurements, and the position lookup."""

import pytest

from tests.conftest import kml

ROWS = [
    ("Alpha", "Polygon"),
    ("beta", "LineString"),
    ("Ünïcode Plot", "Polygon"),
    ("ಬೆಂಗಳೂರು well", "Point"),
    ("Gamma", "LineString"),
] + [(f"Parcel {i:02d}", "Polygon") for i in range(25)]
WITH_ALTITUDE = 1  # "beta": its coordinates carry altitude, giving a Z_DROPPED warning


def geometry(kind: str, i: int) -> str:
    x = 77.59 + i * 0.002
    z = ",5" if i == WITH_ALTITUDE else ""
    if kind == "Point":
        return f"<Point><coordinates>{x},12.97</coordinates></Point>"
    if kind == "LineString":
        coords = f"{x},12.97{z} {x + 0.001},12.971{z}"
        return f"<LineString><coordinates>{coords}</coordinates></LineString>"
    ring = f"{x},12.97 {x + 0.001},12.97 {x + 0.001},12.971 {x},12.971 {x},12.97"
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates>"
        "</LinearRing></outerBoundaryIs></Polygon>"
    )


@pytest.fixture
def fid(client):
    marks = "".join(
        f'<Placemark id="p{i}"><name>{name}</name><ExtendedData><Data name="crop"><value>'
        f"{'ragi' if i % 2 else 'paddy'}</value></Data></ExtendedData>{geometry(kind, i)}"
        "</Placemark>"
        for i, (name, kind) in enumerate(ROWS)
    )
    data = kml(f"<Document>{marks}</Document>")
    response = client.post("/api/files/", files={"file": ("q.kml", data)})
    assert response.status_code == 201
    return response.json()["id"]


def page(client, fid, qs):
    response = client.get(f"/api/files/{fid}/measurements/?{qs}")
    assert response.status_code == 200, response.text
    return response.json()


def names(body):
    return [f["properties"]["name"] for f in body["features"]]


def total(client, fid, qs):
    return page(client, fid, qs)["pagination"]["total"]


def test_no_new_params_means_no_query_field(client, fid):
    assert "query" not in page(client, fid, "limit=5")
    assert "query" not in page(client, fid, "")


def test_any_new_param_adds_query_echo(client, fid):
    body = page(client, fid, "q=&limit=5")
    assert body["query"] == {
        "q": None,
        "geometry": None,
        "status": None,
        "warnings": None,
        "sort": "index",
        "order": "asc",
    }
    assert body["pagination"]["total"] == 30 and body["feature_count"] == 30
    assert names(body) == ["Alpha", "beta", "Ünïcode Plot", "ಬೆಂಗಳೂರು well", "Gamma"]


def test_search_is_unicode_aware(client, fid):
    assert names(page(client, fid, "q=UNICODE")) == []  # ü is not u: no accent stripping
    assert names(page(client, fid, "q=ÜNÏCODE")) == ["Ünïcode Plot"]
    assert names(page(client, fid, "q=ಬೆಂಗಳೂ")) == ["ಬೆಂಗಳೂರು well"]
    assert total(client, fid, "q=crop%20ragi") == 15
    assert total(client, fid, "q=%25") == 0  # no LIKE wildcards
    assert total(client, fid, "q=p29") == 1  # the source id


def test_geometry_status_and_warning_filters(client, fid):
    assert total(client, fid, "geometry=line") == 2
    assert total(client, fid, "geometry=polygon") == 27
    assert names(page(client, fid, "geometry=point")) == ["ಬೆಂಗಳೂರು well"]
    assert total(client, fid, "geometry=other") == 0
    assert total(client, fid, "status=measured") == 29
    assert total(client, fid, "status=MEASURED") == 29
    assert total(client, fid, "status=not_applicable") == 1
    assert total(client, fid, "status=attention") == 0
    assert names(page(client, fid, "warnings=with")) == ["beta"]
    assert total(client, fid, "warnings=without") == 29
    assert total(client, fid, "geometry=polygon&q=parcel%201") == 10


def test_filter_matches_nothing(client, fid):
    body = page(client, fid, "q=zzz")
    assert body["features"] == []
    assert body["pagination"] == {
        "limit": 100,
        "offset": 0,
        "total": 0,
        "returned": 0,
        "next_offset": None,
    }
    assert body["feature_count"] == 30 and body["counts"]["MEASURED"] == 29


def test_sort_by_name_is_unicode_aware(client, fid):
    asc = names(page(client, fid, "sort=name&limit=30"))
    assert asc[:3] == ["Alpha", "beta", "Gamma"]
    assert asc[-2:] == ["Ünïcode Plot", "ಬೆಂಗಳೂರು well"]
    desc = names(page(client, fid, "sort=name&order=desc&limit=30"))
    assert desc == asc[::-1]


def test_measurement_sorts_put_missing_values_last(client, fid):
    for order in ("asc", "desc"):
        features = page(client, fid, f"sort=area&order={order}&limit=1000")["features"]
        areas = [f["area_m2"] for f in features]
        measured = [a for a in areas if a is not None]
        assert measured == sorted(measured, reverse=order == "desc")
        assert areas[len(measured) :] == [None] * (len(areas) - len(measured))
        missing = [f["index"] for f in features if f["area_m2"] is None]
        assert missing == sorted(missing)  # ties broken by file order


def test_sort_by_warnings_and_status(client, fid):
    first = page(client, fid, "sort=warnings&order=desc&limit=1")["features"][0]
    assert first["properties"]["name"] == "beta"
    by_status = page(client, fid, "sort=status&order=desc&limit=1")["features"][0]
    assert by_status["status"] == "NOT_APPLICABLE"


def test_paging_with_filters_covers_all_matches_once(client, fid):
    seen, offset = [], 0
    while offset is not None:
        body = page(client, fid, f"geometry=polygon&sort=name&limit=7&offset={offset}")
        seen += [f["index"] for f in body["features"]]
        offset = body["pagination"]["next_offset"]
    assert len(seen) == len(set(seen)) == 27


@pytest.mark.parametrize(
    "qs",
    [
        "geometry=circle",
        "status=weird",
        "warnings=maybe",
        "sort=size",
        "order=up",
        "q=" + "x" * 201,
    ],
)
def test_invalid_query_values(client, fid, qs):
    response = client.get(f"/api/files/{fid}/measurements/?{qs}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUERY"


@pytest.mark.parametrize("name", ["q", "geometry", "status", "warnings", "sort", "order"])
def test_repeated_new_params_rejected(client, fid, name):
    response = client.get(f"/api/files/{fid}/measurements/?{name}=a&{name}=b")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DUPLICATE_QUERY_PARAMETER"


def test_position_of_matching_feature(client, fid):
    url = f"/api/files/{fid}/features/12/position/?geometry=polygon&sort=name&order=desc&limit=7"
    body = client.get(url).json()
    listing = page(client, fid, "geometry=polygon&sort=name&order=desc&limit=1000")["features"]
    position = [f["index"] for f in listing].index(12)
    assert body == {
        "index": 12,
        "matches": True,
        "position": position,
        "page_offset": position - position % 7,
        "limit": 7,
    }
    on_page = page(
        client, fid, f"geometry=polygon&sort=name&order=desc&limit=7&offset={body['page_offset']}"
    )
    assert 12 in [f["index"] for f in on_page["features"]]


def test_position_uses_the_default_page_size(client, fid):
    body = client.get(f"/api/files/{fid}/features/29/position/").json()
    assert (body["position"], body["page_offset"], body["limit"]) == (29, 0, 100)


def test_position_of_filtered_out_feature(client, fid):
    body = client.get(f"/api/files/{fid}/features/1/position/?geometry=polygon").json()
    assert body == {
        "index": 1,
        "matches": False,
        "position": None,
        "page_offset": None,
        "limit": 100,
    }


def test_position_unknown_index(client, fid):
    response = client.get(f"/api/files/{fid}/features/999/position/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_NOT_FOUND"


def test_position_validates_like_measurements(client, fid):
    response = client.get(f"/api/files/{fid}/features/1/position/?limit=0")
    assert response.json()["error"]["code"] == "INVALID_PAGINATION"
    response = client.get(f"/api/files/{fid}/features/1/position/?sort=size")
    assert response.json()["error"]["code"] == "INVALID_QUERY"


def test_position_of_failed_file_is_409(client):
    response = client.post("/api/files/", files={"file": ("x.zip", b"PK\x03\x04garbage")})
    file_id = response.json()["error"]["file_id"]
    response = client.get(f"/api/files/{file_id}/features/0/position/")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FILE_FAILED"


def test_filters_apply_to_unknown_file_with_404(client):
    response = client.get("/api/files/00000000-0000-4000-8000-000000000000/measurements/?q=x")
    assert response.status_code == 404
