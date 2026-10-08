"""Regenerate the synthetic sample inputs in samples/ (deterministic output).

    uv run python scripts/make_samples.py

All geometries and attributes are invented; the site is an arbitrary spot near Bengaluru.
"""

import io
import tempfile
import zipfile
from pathlib import Path

import fiona
from pyproj import Transformer

OUT = Path(__file__).resolve().parent.parent / "samples"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # constant timestamps keep the ZIPs byte-identical


def utm_origin() -> tuple[int, int]:
    """A round UTM 43N (EPSG:32643) origin near 77.59°E, 12.97°N."""
    x, y = Transformer.from_crs(4326, 32643, always_xy=True).transform(77.59, 12.97)
    return round(x, -3), round(y, -3)


def rect(x0: float, y0: float, width: float, height: float) -> list[tuple[float, float]]:
    # Clockwise, as the Shapefile format expects for outer rings.
    return [(x0, y0), (x0, y0 + height), (x0 + width, y0 + height), (x0 + width, y0), (x0, y0)]


def parcels() -> list[dict]:
    x, y = utm_origin()
    hole = rect(x + 440, y + 40, 40, 40)[::-1]  # counter-clockwise inner ring
    rows = [
        ("P-001", "residential", 101, [rect(x, y, 100, 100)]),
        ("P-002", "agricultural", 102, [rect(x + 150, y, 200, 150)]),
        ("P-003", "agricultural", 103, [rect(x + 400, y, 120, 120), hole]),
    ]
    return [
        {
            "geometry": {"type": "Polygon", "coordinates": rings},
            "properties": {"parcel_id": pid, "land_use": use, "survey_no": no},
        }
        for pid, use, no, rings in rows
    ]


def many_parcels() -> list[dict]:
    """250 small parcels on a 25 x 10 grid: enough for several pages of results."""
    x, y = utm_origin()
    uses = ("residential", "agricultural", "industrial")
    return [
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [rect(x + (i % 25) * 60, y + (i // 25) * 60, 50, 50)],
            },
            "properties": {
                "parcel_id": f"M-{i + 1:03d}",
                "land_use": uses[i % 3],
                "survey_no": 1000 + i,
            },
        }
        for i in range(250)
    ]


def shapefile_zip(
    path: Path, with_prj: bool, records: list[dict] | None = None, folder: str = "parcels"
) -> None:
    schema = {
        "geometry": "Polygon",
        "properties": {"parcel_id": "str:10", "land_use": "str:20", "survey_no": "int"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        shp = Path(tmp) / "parcels.shp"
        with fiona.open(
            shp, "w", driver="ESRI Shapefile", schema=schema, crs="EPSG:32643", encoding="UTF-8"
        ) as dst:
            for record in records or parcels():
                dst.write(fiona.Feature.from_dict(record))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for part in sorted(Path(tmp).glob("parcels.*")):
                if part.suffix == ".prj" and not with_prj:
                    continue
                info = zipfile.ZipInfo(f"{folder}/{part.name}", FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, part.read_bytes())
        path.write_bytes(buffer.getvalue())


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Sample site survey</name>
    <Folder>
      <name>Block A</name>
      <Placemark id="field-1">
        <name>Field 1</name>
        <ExtendedData>
          <Data name="crop"><value>ragi</value></Data>
          <Data name="surveyor"><value>Team 3</value></Data>
        </ExtendedData>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
          77.5900,12.9700 77.5920,12.9700 77.5920,12.9720 77.5900,12.9720 77.5900,12.9700
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
      </Placemark>
      <Placemark id="road-1">
        <name>Access road</name>
        <LineString><coordinates>
          77.5880,12.9690 77.5950,12.9690 77.5980,12.9740
        </coordinates></LineString>
      </Placemark>
      <Placemark id="well-1">
        <name>Borewell</name>
        <ExtendedData><Data name="depth_m"><value>60</value></Data></ExtendedData>
        <Point><coordinates>77.5910,12.9710</coordinates></Point>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


INVALID_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Invalid geometry sample</name>
    <Placemark>
      <name>Valid field</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.5900,12.9700 77.5920,12.9700 77.5920,12.9720 77.5900,12.9720 77.5900,12.9700
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>Unclosed plot</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.5930,12.9700 77.5950,12.9700 77.5950,12.9720 77.5930,12.9720
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>Bow-tie boundary</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.5960,12.9700 77.5980,12.9720 77.5980,12.9700 77.5960,12.9720 77.5960,12.9700
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>Pump house</name>
      <Point><coordinates>77.5905,12.9735</coordinates></Point>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.5900,12.9730 77.5910,12.9730 77.5910,12.9740 77.5900,12.9740 77.5900,12.9730
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>Borewell</name>
      <Point><coordinates>77.5940,12.9740</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    shapefile_zip(OUT / "sample_parcels.zip", with_prj=True)
    shapefile_zip(OUT / "sample_missing_crs.zip", with_prj=False)
    (OUT / "sample_survey.kml").write_text(KML, encoding="utf-8")
    (OUT / "sample_invalid_geometry.kml").write_text(INVALID_KML, encoding="utf-8")
    shapefile_zip(
        OUT / "sample_many_parcels.zip",
        with_prj=True,
        records=many_parcels(),
        folder="many_parcels",
    )
    for p in sorted(OUT.glob("sample_*")):
        print(f"{p.name}: {p.stat().st_size} bytes")
