# Sample inputs

Small synthetic files for trying the API. Geometries and attributes are invented; the site
is an arbitrary location near Bengaluru. Regenerate them with
`uv run python scripts/make_samples.py` (output is byte-identical).

| File | Contents | CRS | Expected result |
|---|---|---|---|
| `sample_parcels.zip` | One Shapefile (`.shp .shx .dbf .prj .cpg`): three parcels, one with a hole | EPSG:32643 (WGS 84 / UTM 43N, metres), from the `.prj` | `COMPLETED`, 3 × `MEASURED` |
| `sample_survey.kml` | Polygon, line and point in a folder, with `ExtendedData` | WGS84 (KML always is) | `COMPLETED`: 2 × `MEASURED`, 1 × `NOT_APPLICABLE` (the point) |
| `sample_missing_crs.zip` | The same parcels without `.prj` | Unknown | `COMPLETED`, 3 × `UNKNOWN_CRS`; with `source_crs=EPSG:32643`, 3 × `MEASURED` |
| `sample_invalid_geometry.kml` | A valid polygon, an unclosed ring, a self-intersecting (bow-tie) ring, a Placemark with two geometries, and a point | WGS84 | `COMPLETED`: 1 × `MEASURED`, 2 × `INVALID_GEOMETRY` (`RING_NOT_CLOSED`, `INVALID_GEOMETRY`), 1 × `UNSUPPORTED_GEOMETRY` (with a `MULTIPLE_GEOMETRIES` warning), 1 × `NOT_APPLICABLE` |
| `sample_many_parcels.zip` | 250 parcels of 50 m × 50 m on a grid, `land_use` cycling residential/agricultural/industrial (83 industrial) | EPSG:32643, from the `.prj` | `COMPLETED`, 250 × `MEASURED`; spans several pages of results |

Values from a verified run (independent references in brackets):

- Parcels: grid areas 10,000, 30,000 and 12,800 m² (120 m × 120 m minus a 40 m × 40 m hole).
  Reported ground areas are 9,988.46, 29,965.31 and 12,785.16 m². UTM 43N's scale factor
  at 77.59°E is about 1.0006 per axis, so ground area is about 0.12 % smaller than grid area
  (PROJ's analytic areal scale agrees to about 1e-10).
- KML field: 48,012.35 m² (geodesic area of the same ring: 48,012.35 m²).
- KML access road: 1,401.30 m (geodesic length: 1,401.30 m).

```bash
curl -F file=@samples/sample_parcels.zip http://127.0.0.1:8000/api/files/
curl -F file=@samples/sample_survey.kml http://127.0.0.1:8000/api/files/
curl -F file=@samples/sample_missing_crs.zip -F source_crs=EPSG:32643 http://127.0.0.1:8000/api/files/
```
