import socket
from pathlib import Path

import pytest

from app.errors import IngestionError
from app.services.dataset import IngestionLimits
from app.services.kml_reader import read_kml
from tests.conftest import LIMITS, kml

SURVEY = """
<Document><name>Survey 2026</name>
  <Schema name="plot" id="plotSchema"><SimpleField name="survey_no" type="int"/></Schema>
  <Folder><name>Ward 12</name>
    <Folder><name>Block A</name>
      <Placemark id="plot-17">
        <name>Plot 17</name>
        <description><![CDATA[<b>Irrigated</b> land]]></description>
        <ExtendedData>
          <Data name="owner"><value>Rao</value></Data>
          <Data name="crop"><displayName>Crop</displayName><value>Ragi</value></Data>
          <SchemaData schemaUrl="#plotSchema">
            <SimpleData name="survey_no">17</SimpleData>
          </SchemaData>
        </ExtendedData>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            77.59,12.97,920 77.60,12.97,921
            77.60,12.98,922   77.59,12.98,920
            77.59,12.97,920
          </coordinates></LinearRing></outerBoundaryIs>
          <innerBoundaryIs><LinearRing><coordinates>
            77.592,12.972 77.594,12.972 77.594,12.974 77.592,12.972
          </coordinates></LinearRing></innerBoundaryIs>
        </Polygon>
      </Placemark>
    </Folder>
    <Placemark><name>Well</name><Point><coordinates>77.595,12.975</coordinates></Point></Placemark>
  </Folder>
  <Placemark><name>Road</name>
    <LineString><coordinates>77.5,12.9 77.6,13.0</coordinates></LineString>
  </Placemark>
</Document>
"""


def read(tmp_path: Path, data: bytes, limits: IngestionLimits = LIMITS):
    path = tmp_path / "upload.kml"
    path.write_bytes(data)
    return read_kml(path, limits)


def expect(code: str, tmp_path: Path, data: bytes, limits: IngestionLimits = LIMITS):
    with pytest.raises(IngestionError) as exc_info:
        read(tmp_path, data, limits)
    assert exc_info.value.code == code


def test_reads_nested_folders_geometry_and_custom_fields(tmp_path):
    dataset = read(tmp_path, kml(SURVEY))
    assert dataset.format == "KML"
    assert dataset.source_crs.status == "KNOWN"
    assert dataset.source_crs.identifier == "EPSG:4326"
    assert dataset.source_crs.origin == "KML_SPECIFICATION"
    assert [f.properties["name"] for f in dataset.features] == ["Plot 17", "Well", "Road"]
    assert [f.index for f in dataset.features] == [0, 1, 2]

    plot, well, road = dataset.features
    assert plot.source_id == "plot-17"
    assert plot.folder_path == ["Survey 2026", "Ward 12", "Block A"]
    assert plot.properties == {
        "name": "Plot 17",
        "description": "<b>Irrigated</b> land",
        "owner": "Rao",
        "crop": "Ragi",
        "survey_no": "17",  # KML text is kept as text; no type coercion
    }
    assert plot.geometry_type == "Polygon"
    outer, hole = plot.geometry["coordinates"]
    assert outer[0] == [77.59, 12.97, 920.0]  # longitude first, altitude kept
    assert len(outer) == 5
    assert hole[1] == [77.594, 12.972]

    assert well.folder_path == ["Survey 2026", "Ward 12"]
    assert well.geometry == {"type": "Point", "coordinates": [77.595, 12.975]}
    assert well.source_id is None
    assert road.geometry == {"type": "LineString", "coordinates": [[77.5, 12.9], [77.6, 13.0]]}


@pytest.mark.parametrize(
    "namespace",
    [
        "http://www.opengis.net/kml/2.2",
        "http://earth.google.com/kml/2.0",
        "http://earth.google.com/kml/2.1",
        "http://earth.google.com/kml/2.2",
        None,
    ],
)
def test_allowlisted_namespaces(tmp_path, namespace):
    dataset = read(tmp_path, kml(SURVEY, namespace=namespace))
    assert len(dataset.features) == 3
    assert dataset.features[0].properties["owner"] == "Rao"


def test_unknown_namespace_rejected(tmp_path):
    expect("INVALID_KML", tmp_path, kml(SURVEY, namespace="http://example.com/not-kml"))


def test_wrong_root_rejected(tmp_path):
    expect("INVALID_KML", tmp_path, b"<gpx><trk/></gpx>")


@pytest.mark.parametrize("data", [b"<kml><Document>", b"not xml at all", b"\xff\xfe\x00"])
def test_malformed_xml(tmp_path, data):
    expect("INVALID_KML", tmp_path, data)


def test_coordinate_whitespace_variants(tmp_path):
    body = """<Placemark><LineString><coordinates>
        77.5,12.9,10\t77.6 , 13.0
        \n\n   77.7,13.1
    </coordinates></LineString></Placemark>"""
    feature = read(tmp_path, kml(body)).features[0]
    assert feature.geometry["coordinates"] == [[77.5, 12.9, 10.0], [77.6, 13.0], [77.7, 13.1]]


def test_gx_extensions_ignored(tmp_path):
    body = """<Placemark><name>x</name><gx:balloonVisibility>1</gx:balloonVisibility>
      <Point><coordinates>1,2</coordinates></Point></Placemark>"""
    data = kml(body).replace(b"<kml ", b'<kml xmlns:gx="http://www.google.com/kml/ext/2.2" ')
    assert read(tmp_path, data).features[0].geometry == {"type": "Point", "coordinates": [1.0, 2.0]}


def test_multigeometry_homogeneous_becomes_multi(tmp_path):
    body = """<Placemark><MultiGeometry>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>0,0 1,0 1,1 0,0</coordinates>
      </LinearRing></outerBoundaryIs></Polygon>
      <MultiGeometry><Polygon><outerBoundaryIs><LinearRing>
        <coordinates>2,2 3,2 3,3 2,2</coordinates></LinearRing></outerBoundaryIs></Polygon>
      </MultiGeometry>
    </MultiGeometry></Placemark>"""
    feature = read(tmp_path, kml(body)).features[0]
    assert feature.geometry_type == "MultiPolygon"
    assert len(feature.geometry["coordinates"]) == 2


def test_multigeometry_mixed_becomes_collection(tmp_path):
    body = """<Placemark><MultiGeometry>
      <Point><coordinates>0,0</coordinates></Point>
      <LineString><coordinates>0,0 1,1</coordinates></LineString>
    </MultiGeometry></Placemark>"""
    feature = read(tmp_path, kml(body)).features[0]
    assert feature.geometry_type == "GeometryCollection"
    assert [g["type"] for g in feature.geometry["geometries"]] == ["Point", "LineString"]


def test_linear_ring_placemark_is_a_closed_line(tmp_path):
    body = (
        "<Placemark><LinearRing><coordinates>0,0 1,0 1,1 0,0</coordinates></LinearRing></Placemark>"
    )
    feature = read(tmp_path, kml(body)).features[0]
    assert feature.geometry_type == "LineString"


def test_gx_track_is_kept_as_unsupported(tmp_path):
    body = """<Placemark><name>drive</name><gx:Track><when>2026-01-01</when>
      <gx:coord>1 2 3</gx:coord></gx:Track></Placemark>
      <Placemark><name>ok</name><Point><coordinates>1,2</coordinates></Point></Placemark>"""
    data = kml(body).replace(b"<kml ", b'<kml xmlns:gx="http://www.google.com/kml/ext/2.2" ')
    track, ok = read(tmp_path, data).features
    assert track.issue_code == "UNSUPPORTED_GEOMETRY"
    assert track.geometry is None
    assert track.properties["name"] == "drive"
    assert ok.issue_code is None


@pytest.mark.parametrize(
    "coords",
    ["1,2,3,4", "east,north", "nan,1", "inf,2", "1"],
)
def test_bad_coordinates_affect_only_that_feature(tmp_path, coords):
    body = f"""<Placemark><name>bad</name><LineString><coordinates>{coords} 5,6</coordinates>
      </LineString></Placemark>
      <Placemark><name>good</name><Point><coordinates>1,2</coordinates></Point></Placemark>"""
    bad, good = read(tmp_path, kml(body)).features
    assert bad.issue_code == "INVALID_COORDINATES"
    assert bad.geometry is None
    assert good.geometry is not None


def test_placemark_without_geometry_kept(tmp_path):
    feature = read(tmp_path, kml("<Placemark><name>note</name></Placemark>")).features[0]
    assert feature.geometry is None
    assert feature.issue_code is None
    assert feature.properties == {"name": "note"}


def test_property_key_collision_is_not_lost(tmp_path):
    body = """<Placemark><name>Plot</name><ExtendedData>
      <Data name="name"><value>Survey name</value></Data></ExtendedData></Placemark>"""
    feature = read(tmp_path, kml(body)).features[0]
    assert feature.properties == {"name": "Plot", "name__2": "Survey name"}
    assert [w["code"] for w in feature.warnings] == ["PROPERTY_KEY_COLLISION"]


def test_external_entity_xxe_rejected(tmp_path):
    data = b"""<?xml version="1.0"?>
<!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>&xxe;</name></Placemark></kml>"""
    expect("UNSAFE_XML", tmp_path, data)


def test_billion_laughs_rejected(tmp_path):
    data = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<kml><Placemark><name>&lol3;</name></Placemark></kml>"""
    expect("UNSAFE_XML", tmp_path, data)


def test_network_link_is_never_fetched(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    body = """<Document>
      <NetworkLink><Link><href>http://example.com/evil.kml</href></Link></NetworkLink>
      <GroundOverlay><Icon><href>http://example.com/tile.png</href></Icon></GroundOverlay>
      <Placemark><Point><coordinates>1,2</coordinates></Point></Placemark></Document>"""
    dataset = read(tmp_path, kml(body))
    assert len(dataset.features) == 1
    assert sorted(w["code"] for w in dataset.warnings) == [
        "NETWORK_LINK_IGNORED",
        "OVERLAY_IGNORED",
    ]


def test_renamed_kmz_is_reported_as_kmz(tmp_path):
    expect("KMZ_UNSUPPORTED", tmp_path, b"PK\x03\x04rest-of-a-zip")


def test_deep_folder_nesting_does_not_crash(tmp_path):
    depth = 5000
    body = (
        "<Folder><name>f</name>" * depth
        + "<Placemark><Point><coordinates>1,2</coordinates></Point></Placemark>"
    )
    body += "</Folder>" * depth
    feature = read(tmp_path, kml(body)).features[0]
    assert len(feature.folder_path) == depth


def test_feature_and_vertex_limits_fail_instead_of_truncating(tmp_path):
    points = "<Placemark><Point><coordinates>1,2</coordinates></Point></Placemark>" * 3
    expect("TOO_MANY_FEATURES", tmp_path, kml(points), IngestionLimits(10**8, 10, 2, 100))
    line = "<Placemark><LineString><coordinates>0,0 1,1 2,2</coordinates></LineString></Placemark>"
    expect("TOO_MANY_VERTICES", tmp_path, kml(line), IngestionLimits(10**8, 10, 10, 2))
