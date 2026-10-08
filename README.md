# AEREO Geospatial File Measurement API

A FastAPI service that accepts a zipped Shapefile or a KML file, extracts its features, resolves the
coordinate reference system (CRS), measures polygon area and line length, and returns the results
through a small REST API and an interactive web workspace.

Every feature is reported with an explicit status. A file can finish processing (`COMPLETED`) while some
of its features are skipped or invalid, and the response says which ones and why. Coordinates are never
measured in degrees, and a missing CRS is never guessed.

**Stack:** Python 3.12, FastAPI, Fiona (GDAL), Shapely 2, pyproj (PROJ), SQLAlchemy 2 + SQLite,
defusedxml, plain HTML/CSS/JavaScript, bundled Leaflet 1.9.4, pytest/Playwright, Ruff, uv, Docker,
GitHub Actions. The frontend needs no Node build or separate service.

## Contents

- [Quick start](#quick-start)
- [Web workspace](#web-workspace)
- [API](#api)
- [Architecture](#architecture)
- [Measurement methodology](#measurement-methodology)
- [Feature statuses and errors](#feature-statuses-and-errors)
- [Security](#security)
- [Design decisions](#design-decisions)
- [Limitations](#limitations)
- [Testing](#testing)
- [Learnings](#learnings)
- [Future scope](#future-scope)

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if needed).

```bash
git clone https://github.com/Cyril-36/aereo-geospatial-api.git
cd aereo-geospatial-api
uv sync --frozen                      # exact dependency versions from uv.lock
uv run uvicorn app.main:app --port 8000
```

Open <http://127.0.0.1:8000/> for the workspace, or <http://127.0.0.1:8000/docs> for the
interactive OpenAPI documentation. Try a sample:

```bash
curl -F file=@samples/sample_parcels.zip http://127.0.0.1:8000/api/files/
```

Data (the SQLite database and stored uploads) goes to `./data`; set `AEREO_DATA_DIR` to change it. The
schema is created or upgraded automatically at startup.

Run the checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

### Docker

The supported image platform is **linux/amd64** (see [Limitations](#limitations)).

```bash
docker build --platform linux/amd64 -t aereo-geospatial-api .
docker run --platform linux/amd64 -p 8000:8000 -v aereo-data:/data aereo-geospatial-api
# or
docker compose up --build
```

The container runs one Uvicorn worker as a non-root user and keeps its database and uploads on the
`/data` volume, so results survive container restarts.

### Configuration

All settings are environment variables with the `AEREO_` prefix (see `app/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `AEREO_DATA_DIR` | `data` (`/data` in Docker) | Database and uploads |
| `AEREO_MAX_UPLOAD_BYTES` | 10 MiB | Upload size limit, counted as bytes arrive |
| `AEREO_MAX_EXPANDED_BYTES` | 100 MiB | Total uncompressed size of a ZIP |
| `AEREO_MAX_ARCHIVE_MEMBERS` | 1,000 | Entries in a ZIP |
| `AEREO_MAX_FEATURES` | 20,000 | Features per file |
| `AEREO_MAX_VERTICES` | 1,000,000 | Vertices per file, including densification |
| `AEREO_DENSIFY_MAX_SEGMENT_M` | 50,000 | Longest edge segment before densifying, in metres |
| `AEREO_MAX_VERTICES_PER_FEATURE` | 200,000 | Vertices per feature after densification |
| `AEREO_MAX_PAGE_SIZE` | 1,000 | Largest `limit` for the measurements endpoint |
| `AEREO_MAP_TILE_URL` | OSM standard tile URL | Basemap template; empty disables external tiles |
| `AEREO_MAP_MAX_FEATURES` | 5,000 | Features inspected in the initial map preview |
| `AEREO_MAP_MAX_VERTICES` | 250,000 | Vertices drawn in the initial map preview |
| `AEREO_HISTORY_DEFAULT_PAGE_SIZE` | 50 | Default history page size |
| `AEREO_HISTORY_MAX_PAGE_SIZE` | 200 | Maximum history page size |
| `AEREO_MAX_SEARCH_CHARS` | 200 | Maximum feature/history search length |

## Web workspace

Use the same local or Docker startup commands above, then open <http://127.0.0.1:8000/>.

- **Upload (`/`):** choose or drop a ZIP/KML, optionally supply the source CRS, and see real
  upload progress followed by an indeterminate processing state. Ambiguous connection failures
  offer a history check before resubmission; uploads are never automatically retried.
- **Results (`/files/{id}`):** inspect statuses, warning explanations, properties and measurement
  details. Search and sort the complete dataset, page through it, switch display units and select
  features through the table or map. Filters, page and selection survive refresh and Back/Forward.
- **History (`/history`):** search by filename/ID, filter by status or format, reopen results and
  confirm deletion. History is stored in SQLite and survives application restarts.
- **Downloads:** CSV contains measurements, statuses and warnings; JSON also includes attributes,
  geometry, CRS and transformation metadata. Choose all features or the filtered set. Every matching
  feature is exported across all pages, with full numeric precision and canonical m²/metre units.

The map is an informational preview; its drawing never supplies measurements. Only supported,
usable geometry labelled EPSG:4326 is plotted. Invalid but drawable boundaries can be inspected
with their unsuccessful measurement status; unknown/projected source coordinates are not guessed.
The map reports skipped features and limits, loads incrementally, and remains independent of the
table. Initial loading stops at the configured feature/vertex budgets; **Load more** raises those
budgets once, up to twice their configured values. Vertex checks occur after receiving a page.
Changing filters, files or routes cancels old loads. The sums of feature areas/lengths include
measured features only; overlapping polygons are not merged into a land footprint.

Leaflet and its license are bundled locally. The default basemap uses OpenStreetMap with visible
attribution and browser caching; follow the [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).
To run without external tiles, set `AEREO_MAP_TILE_URL=""`. Measurements and vectors still work if
the basemap is unavailable. Browser tests disable tiles and reject external requests.

Deletion removes the database record and features, then the stored upload. After an I/O error,
upload cleanup may be pending even though the API no longer serves the record. Startup cleanup
retries orphan uploads older than ten minutes; recent files are retained to protect active uploads.
An export uses one WAL read snapshot, so deletion during streaming leaves the download complete.

This is a single shared workspace with no authentication or per-user ownership, intended for the
assignment/local demo. Existing requests to the three required endpoints preserve their original
responses; feature query changes are opt-in and covered by compatibility snapshots.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/files/` | Upload and process a file |
| `GET` | `/api/files/{id}/` | File information and per-status feature counts |
| `GET` | `/api/files/{id}/measurements/` | Paginated per-feature results |
| `GET` | `/api/config/` | Configured upload, pagination and map limits |
| `GET` | `/api/files/` | Persistent, paginated upload history |
| `DELETE` | `/api/files/{id}/` | Delete a file and its results |
| `GET` | `/api/files/{id}/features/{index}/position/` | Locate a feature in filtered/sorted results |
| `GET` | `/api/files/{id}/export/` | Stream complete CSV or JSON results |

### Workspace API additions

```bash
curl http://127.0.0.1:8000/api/config/
curl 'http://127.0.0.1:8000/api/files/?q=survey&status=COMPLETED&limit=50'
curl 'http://127.0.0.1:8000/api/files/FILE_ID/measurements/?q=industrial&sort=name&order=asc'
curl 'http://127.0.0.1:8000/api/files/FILE_ID/features/240/position/?sort=name&limit=100'
curl -OJ 'http://127.0.0.1:8000/api/files/FILE_ID/export/?format=csv&status=attention'
curl -OJ 'http://127.0.0.1:8000/api/files/FILE_ID/export/?format=json'
curl -X DELETE http://127.0.0.1:8000/api/files/FILE_ID/
```

Replace `FILE_ID` with the upload response ID. History accepts `q`, `status`, `format`, `limit`,
`offset` and `since` (an ISO timestamp with a timezone). Search normalises Unicode and is case
insensitive; newest records come first.

Measurements, position and export share these optional query parameters:

| Parameter | Values |
|---|---|
| `q` | Substring of feature name, source ID or property keys/values |
| `geometry` | `polygon`, `line`, `point`, `other` |
| `status` | `measured`, `not_applicable`, `attention`, or a raw feature status |
| `warnings` | `with`, `without` |
| `sort` | `index`, `name`, `status`, `area`, `length`, `warnings` |
| `order` | `asc`, `desc` |

Missing sort values come last in either direction, with index breaking ties. Filtered
`pagination.total` counts matches; `feature_count` and `counts` remain whole-file values.
The `query` echo appears only when a new query parameter is supplied. Repeated parameters are
rejected. Position returns `matches`, the zero-based rank and `page_offset`; a filtered-out feature
has null rank/offset. Exports require `format=csv|json`, share the filters/sort, and ignore pagination.

CSV columns: `index`, `source_id`, `name`, `geometry_type`, `status`, `reason_code`, `reason`,
`area_m2`, `length_m`, `measurement_method`, `measurement_crs`, `geodesic_relative_difference`,
`warning_codes`, `warning_messages`, `folder_path`. UTF-8 with a BOM preserves non-ASCII text in
spreadsheets. Formula-like text is prefixed with an apostrophe; numeric measurements are unchanged.
JSON includes file metadata, export scope/filter/unit information and complete feature results.

The responses below are from a real run of the Docker image with the files in [`samples/`](samples/).

### `POST /api/files/`

Multipart form fields:

- `file` (required): a `.zip` containing exactly one Shapefile, or a `.kml` file.
- `source_crs` (optional): an EPSG code (`EPSG:32643` or `32643`) or WKT. It supplies a CRS when the
  file has none (for example, a Shapefile without `.prj`). If the file declares a different valid CRS,
  the upload fails with `422 CRS_CONFLICT`; it never overrides a declared CRS.

Each field may be sent at most once. A repeated `file` or `source_crs`, even with an identical
value, is rejected with `422 DUPLICATE_FORM_FIELD` before anything is stored; it is never resolved
by silently using one of the values.

Processing is synchronous: the response arrives once the file has been read, measured and stored.

```bash
curl -i -F file=@samples/sample_parcels.zip http://127.0.0.1:8000/api/files/
```

```
HTTP/1.1 201 Created
location: /api/files/b6807c2f-0cb5-4880-a9ae-d4038e8d6b68/
```

```json
{
  "id": "b6807c2f-0cb5-4880-a9ae-d4038e8d6b68",
  "filename": "sample_parcels.zip",
  "format": "SHAPEFILE",
  "size_bytes": 1163,
  "sha256": "acd65d25a1023c73020ecf0353db717d376de15efe67b3096451183cb41cbfe0",
  "status": "COMPLETED",
  "feature_count": 3,
  "counts": { "MEASURED": 3 },
  "crs": "EPSG:32643",
  "crs_status": "KNOWN",
  "crs_origin": "PRJ_FILE",
  "transformation": {
    "operation": "Inverse of UTM zone 43N (with axis order normalized for visualization)",
    "accuracy_m": null,
    "best_available": true
  },
  "warnings": [],
  "error": null,
  "created_at": "2026-10-08T11:30:38.339230Z",
  "processed_at": "2026-10-08T11:30:38.619249Z"
}
```

A Shapefile without `.prj` still completes, but nothing is measured:

```bash
curl -F file=@samples/sample_missing_crs.zip http://127.0.0.1:8000/api/files/
```

```json
{
  "status": "COMPLETED",
  "feature_count": 3,
  "counts": { "UNKNOWN_CRS": 3 },
  "crs": null,
  "crs_status": "UNKNOWN",
  "warnings": [{ "code": "CRS_MISSING", "message": "No .prj file: the source CRS is unknown." }]
}
```

Supplying the CRS measures it:

```bash
curl -F file=@samples/sample_missing_crs.zip -F source_crs=EPSG:32643 http://127.0.0.1:8000/api/files/
```

```json
{ "status": "COMPLETED", "counts": { "MEASURED": 3 }, "crs": "EPSG:32643", "crs_origin": "OVERRIDE" }
```

A conflicting `source_crs` is rejected; the failed file keeps its id:

```json
{
  "error": {
    "code": "CRS_CONFLICT",
    "message": "source_crs EPSG:4326 conflicts with the file's declared CRS EPSG:32643.",
    "file_id": "5342a431-b3e7-4bc5-8277-0d36176e7fe0"
  }
}
```

### `GET /api/files/{id}/`

Returns the same object as the upload response: filename, format, size, status, `feature_count`,
`counts` (features per status), the CRS and where it came from (`PRJ_FILE`, `KML_SPECIFICATION`,
`OVERRIDE` or `NONE`), the transformation used, warnings, any error, and timestamps.

`COMPLETED` means the file was processed and stored, **not** that every feature was measurable. Check
`counts`: `{"MEASURED": 2, "INVALID_GEOMETRY": 1}` is a completed file with one invalid feature.

### `GET /api/files/{id}/measurements/`

Query parameters: `limit` (default 100, maximum 1,000) and `offset` (default 0). Features are always
ordered by their position in the source file (`index`). `pagination.next_offset` is `null` on the last page,
and an offset past the end returns an empty page. Repeating `limit` or `offset` is rejected with
`422 DUPLICATE_QUERY_PARAMETER`.

```bash
curl "http://127.0.0.1:8000/api/files/b6807c2f-0cb5-4880-a9ae-d4038e8d6b68/measurements/?limit=1"
```

```json
{
  "file_id": "b6807c2f-0cb5-4880-a9ae-d4038e8d6b68",
  "status": "COMPLETED",
  "crs": "EPSG:32643",
  "feature_count": 3,
  "counts": { "MEASURED": 3 },
  "pagination": { "limit": 1, "offset": 0, "total": 3, "returned": 1, "next_offset": 1 },
  "features": [
    {
      "index": 0,
      "source_id": "0",
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.59012942868335, 12.967792239505222], [77.59013877947528, 12.968695583940013],
                         [77.59105987333166, 12.968686411367727], [77.59105051922351, 12.967783067593365],
                         [77.59012942868335, 12.967792239505222]]]
      },
      "geometry_crs": "EPSG:4326",
      "geometry_origin": "TRANSFORMED",
      "source_crs": "EPSG:32643",
      "properties": { "parcel_id": "P-001", "land_use": "residential", "survey_no": 101 },
      "folder_path": [],
      "status": "MEASURED",
      "reason_code": null,
      "reason": null,
      "area_m2": 9988.464368975392,
      "length_m": null,
      "measurement_method": "LOCAL_LAEA",
      "measurement_crs": "+proj=laea +lat_0=12.968239325873668 +lon_0=77.59059465012795 +datum=WGS84 +units=m +no_defs",
      "geodesic_reference": { "area_m2": 9988.464369316585, "length_m": null, "relative_difference": 3.4158734193357443e-11 },
      "transformation": { "operation": "Inverse of UTM zone 43N (with axis order normalized for visualization)", "accuracy_m": null, "best_available": true },
      "generated_vertices": 0,
      "warnings": []
    }
  ]
}
```

This parcel is 100 m × 100 m on the UTM grid. Its ground area is 9,988.46 m² because UTM 43N's scale
factor at this longitude is about 1.0006 per axis.

How `geometry` is labelled:

- `geometry_origin: "TRANSFORMED"`: the original vertices converted to WGS84; `geometry_crs` is
  `EPSG:4326`.
- `geometry_origin: "SOURCE"`: the original coordinates, unchanged. This is used when the feature was not
  transformed, for example when the CRS is unknown or the transformation failed. `geometry_crs` is the
  source CRS, or `null` when it is unknown. Original coordinates are never labelled as WGS84.

`area_m2` and `length_m` are `null` when not calculated; `0` would be a calculated zero. Values are
returned at full floating-point precision, and responses never contain `NaN` or `Infinity`.

KML features keep their `name`, `description`, `ExtendedData` and `SchemaData` values in `properties`, and
their folder path in `folder_path`. A Placemark with several geometries (KML allows one) keeps all of them
as one multi-geometry, with a `MULTIPLE_GEOMETRIES` warning. In the sample survey file:

```json
{"index": 0, "source_id": "field-1", "geometry_type": "Polygon",    "status": "MEASURED",       "area_m2": 48012.3461844588, "length_m": null,               "folder_path": ["Sample site survey", "Block A"]}
{"index": 1, "source_id": "road-1",  "geometry_type": "LineString", "status": "MEASURED",       "area_m2": null,             "length_m": 1401.2980533039452, "folder_path": ["Sample site survey", "Block A"]}
{"index": 2, "source_id": "well-1",  "geometry_type": "Point",      "status": "NOT_APPLICABLE", "area_m2": null,             "length_m": null,               "folder_path": ["Sample site survey", "Block A"]}
```

### Errors

All errors use one envelope: `{"error": {"code": "...", "message": "...", "file_id": "..."}}`.
`file_id` is present when a file record was created.

| Status | When | Example codes |
|---|---|---|
| 404 | Unknown file id | `FILE_NOT_FOUND` |
| 409 | Measurements requested for a file that is not `COMPLETED` | `FILE_NOT_READY`, `FILE_FAILED`, `MEASUREMENTS_UNAVAILABLE` |
| 413 | Upload, archive, form, feature or vertex limit exceeded | `UPLOAD_TOO_LARGE`, `EXPANDED_SIZE_EXCEEDED`, `FORM_LIMIT_EXCEEDED`, `TOO_MANY_FEATURES` |
| 415 | Unsupported format | `UNSUPPORTED_FORMAT`, `KMZ_UNSUPPORTED` |
| 422 | Invalid input or dataset | `EMPTY_FILE`, `INVALID_ZIP`, `MALFORMED_DATASET`, `MULTIPLE_DATASETS_UNSUPPORTED`, `MISSING_SHAPEFILE_COMPONENT`, `INVALID_KML`, `UNSAFE_XML`, `CRS_CONFLICT`, `INVALID_SOURCE_CRS`, `DUPLICATE_FORM_FIELD`, `DUPLICATE_QUERY_PARAMETER`, `INVALID_PAGINATION`, `MALFORMED_REQUEST` |
| 500 | Unexpected error | `INTERNAL_ERROR`: no internal details, only a request id that matches the server log |

## Architecture

```
Upload ──► Validate ──► Extract ──► Resolve CRS ──► Measure ──► Persist ──► Retrieve
```

| Module | Responsibility |
|---|---|
| `app/api/files.py` | The three endpoints; status lifecycle; error mapping |
| `app/middleware.py` | Request-body size guard, applied before multipart parsing |
| `app/services/storage.py` | Streamed upload with a measured byte limit, SHA-256, random file names |
| `app/services/safe_zip.py` | Validates every ZIP entry, then extracts only the Shapefile's components |
| `app/services/shapefile_reader.py` | Reads the Shapefile with Fiona; fails on corruption GDAL only logs |
| `app/services/kml_reader.py` | Hardened KML reader built on defusedxml |
| `app/services/crs.py` | Describes the declared CRS, applies `source_crs`, selects the transformation |
| `app/services/measurements.py` | Guards, densification, local projection, geodesic check, feature statuses |
| `app/services/processor.py` | Runs extraction and measurement with the configured limits |
| `app/results.py` | Maps results to records and responses; refuses inconsistent results |
| `app/migrations.py` | Versioned, forward-only schema migrations |

Parsing, CRS and measurement code is independent of HTTP and the database, and is tested directly.

File-processing flow:

1. Check the extension (`.zip` or `.kml`; `.kmz` is rejected with a clear message) and the optional
   `source_crs`.
2. Stream the upload to disk under a random name, counting bytes, and store a `PROCESSING` record.
3. Validate the container: ZIP entries are checked (paths, sizes, symlinks, encryption, nested
   archives) before anything is written; KML is parsed with entities and DTDs disabled.
4. Read every feature, keeping source identifiers, attributes and original coordinates. A problem in one
   feature (bad coordinates, an unsupported geometry) is recorded on that feature only.
5. Resolve the CRS, then measure each feature (next section).
6. Store all features and mark the file `COMPLETED` in a single transaction. On an expected failure the
   file becomes `FAILED` with an error code; nothing partial is stored. Any file left `PROCESSING` by a
   crash is marked `FAILED` (`INTERRUPTED`) at the next startup.

## Measurement methodology

1. **Original geometry is preserved.** Measurements use a 2D working copy; altitude is dropped there, with
   a `Z_DROPPED` warning.
2. **The CRS is resolved, never guessed.** KML is WGS84 by specification. A Shapefile's CRS comes from its
   `.prj` or from `source_crs`. Without either, the features are `UNKNOWN_CRS` and get no numbers.
3. **Conversion to WGS84.** pyproj's `TransformerGroup` (with `always_xy=True`, so longitude comes first)
   picks the best transformation that is available offline; PROJ network access is disabled.
   - The operation is chosen for where the data is: the file's longitude/latitude extent is passed as
     an area of interest, so NAD27 data in California gets a US operation, not a Canadian one.
   - The operation and its stated accuracy are recorded.
   - `NON_BEST_TRANSFORMATION`: a better operation needs grid files that are not installed.
   - `TRANSFORMATION_ACCURACY_UNKNOWN`: a datum change has no stated accuracy (e.g. a ballpark
     fallback).
   - `TRANSFORMATION_OUTSIDE_AREA_OF_USE`: the selected operation does not cover all of the data.

   A datum error mainly moves the geometry. For a local feature, a shift of tens of metres changes
   its measured size very little, but positions (and the returned WGS84 geometry) can be off by the
   operation's stated accuracy or more.
4. **Guards.** These are checked before measuring, and again after densification:
   - geometry crossing the 180° meridian;
   - a vertex more than 90° of arc from the feature's centre;
   - coordinates outside a projected CRS's valid domain (checked by an inverse-then-forward round trip
     with a 1 mm tolerance, an empirical safeguard rather than an accuracy guarantee);
   - non-finite values.

   Each of these fails the feature, not the file.
5. **Densification to 50 km.** Long edges are split before projecting:
   - In geographic sources, edges are geodesics, so points are inserted along the WGS84 geodesic.
   - In projected sources, edges are straight lines in the source CRS, so points are inserted there. The
     50 km step is converted to the CRS's units first: a State Plane CRS in US survey feet uses about
     164,042 ft, not 50,000.

   Insert counts are checked against the vertex limits before anything is generated.
6. **Local equal-area projection.** Each feature is projected to a Lambert Azimuthal Equal-Area (LAEA)
   CRS centred on that feature. Area is read in m² (holes subtracted, parts added) and length in metres.
7. **Geodesic cross-check.** The same densified geometry is measured on the WGS84 ellipsoid with
   `pyproj.Geod` (Karney's algorithm). The reference is returned with the result. A difference over
   0.1 % (with a small absolute floor) adds `GEODESIC_DISAGREEMENT`; a zero reference reports only the
   absolute difference. This is a quality signal, not proof of correctness.
8. **Numerical validation.** A `MEASURED` feature always has a finite, non-negative value and reference.
   Anything else becomes `TRANSFORM_FAILED` (`NON_FINITE_MEASUREMENT` or `NEGATIVE_MEASUREMENT`). The
   storage layer also refuses any result whose numbers contradict its status.

LAEA is equal-area, not distance-preserving. Within the supported extent (features that do not cross
the 180° meridian and stay within 90° of arc of their centre) and with edges densified to 50 km, area
closely matches the geodesic reference. In testing, a 10° × 10° polygon agreed to 0.0007 %. This
assumes edges are geodesics for geographic sources and straight lines in the source CRS for projected
sources, and it relies on the source CRS and datum transformation being correct. Lengths are approximate.
For local features the error is negligible (about 0.004 % at 100 km from the centre). Lines spanning
hundreds of kilometres can be off by about 0.1 % or more, which the geodesic check flags.

## Feature statuses and errors

| Status | Meaning |
|---|---|
| `MEASURED` | Area (polygons) or length (lines) was calculated |
| `NOT_APPLICABLE` | Point or MultiPoint: nothing to measure |
| `UNKNOWN_CRS` | The file declares no CRS and none was supplied |
| `CRS_UNSUPPORTED` | The CRS cannot be parsed or converted to WGS84 (e.g. a local engineering CRS) |
| `EMPTY_GEOMETRY` | The feature has no coordinates |
| `INVALID_GEOMETRY` | Malformed or invalid geometry (e.g. a self-intersecting polygon, an unclosed ring, or a polygon that becomes invalid once its edges follow geodesics); it is reported, never repaired |
| `UNSUPPORTED_GEOMETRY` | A type that is not measured, e.g. a mixed GeometryCollection or `gx:Track` |
| `UNSUPPORTED_EXTENT` | Crosses the 180° meridian, spans a hemisphere, or exceeds the densification limits |
| `TRANSFORM_FAILED` | Conversion failed, a coordinate is outside the projection's domain, or the result is not a finite, non-negative number |

The first applicable rule wins, in this order:

1. a problem recorded by the reader;
2. empty geometry;
3. point;
4. unsupported type;
5. invalid geometry;
6. CRS;
7. conversion;
8. extent;
9. measurement.

Every non-`MEASURED` feature has a `reason_code` and a human-readable `reason`.

## Security

- **Uploads:** the size limit is enforced on the bytes actually received, both for the request body and
  for the stored file. `Content-Length`, the file name and the MIME type are not trusted.
- **ZIP archives:**
  - Every entry is validated before anything is written.
  - Rejected: absolute and `..` paths, symlinks, encrypted entries, paths that collide by case or Unicode
    form, nested archives, and archives over the entry-count or uncompressed-size limits.
  - Only the Shapefile's own components are extracted, under fixed names, into a temporary directory that
    is always removed. `extractall` is never used.
- **KML:**
  - Parsed with defusedxml; DTDs, entities and external references are refused.
  - `NetworkLink` and overlays are skipped with a warning, and nothing is fetched.
- **No CRS guessing:** coordinates that look like longitude/latitude are not assumed to be WGS84.
- **Errors:** 500 responses contain no stack trace or internal detail. Logs record ids and error codes,
  never attribute values.
- **Docker:** the container runs as a non-root user.

## Design decisions

| Decision | Alternatives considered | Reason |
|---|---|---|
| FastAPI | Django + DRF | Small surface, automatic OpenAPI docs, no ORM or admin needed |
| Local LAEA per feature | UTM zone of the centroid | UTM is not equal-area: about −0.08 % to +0.18 % area error within a zone at 13°N, versus about 10⁻⁹ for local LAEA on the same squares. It also has zone edges (78°E runs between Bengaluru and Hyderabad) and fails near the poles. |
| Geodesic cross-check stored with each result | Trust the projection alone | Makes distortion visible, especially for long lines |
| Own KML reader (defusedxml) | GDAL's KML driver | The Fiona wheel's GDAL has no KML or LIBKML driver, and a small reader lets XML hardening be controlled directly |
| Report invalid geometry, never repair | `make_valid` | A repair can change the geometry type and its meaning, and the measurement would describe a different shape |
| Synchronous processing | Background tasks or a queue | Simpler and testable; the result is in the response. Needs a recovery policy for crashes, which is implemented. |
| SQLite and a small migration runner | PostgreSQL/PostGIS, Alembic | No spatial queries are needed. Migrations are forward-only, transactional and tested against a real earlier database. |

## Limitations

- **Formats:** one Shapefile dataset per ZIP; zipped Shapefile and KML only. KMZ is rejected; unzip it and
  upload the `.kml`.
- **Extent:** features that cross the 180° meridian or span a hemisphere are `UNSUPPORTED_EXTENT`. That
  includes projected data that legitimately crosses the dateline.
- **Lengths:** LAEA is not distance-preserving, so long lines are approximate (flagged by the geodesic
  check).
- **Single process:** SQLite and startup recovery assume a single process (one Uvicorn worker). Several
  processes would mark each other's in-flight uploads `INTERRUPTED`.
- **Synchronous processing:** a file at the 20,000-feature limit is processed inside the request (about
  9 s for 20,000 small polygons on an Apple M2).
- **Transformation grids:** PROJ grids are not installed and network access is disabled, so some datum
  transformations fall back to less accurate operations, flagged as `NON_BEST_TRANSFORMATION`.
- **Platform:** the Docker image is supported on linux/amd64 only. Fiona 1.10.1 publishes no linux/arm64
  wheel; Apple Silicon can run the amd64 image through emulation.
- **Legacy files:** files stored by the first, extraction-only version keep the status `EXTRACTED`. They
  were never measured, and their measurements endpoint answers `409 MEASUREMENTS_UNAVAILABLE`; upload
  them again to measure them.

## Testing

```bash
uv run pytest
uv run playwright install chromium
uv run pytest -m e2e
```

The backend suite uses real files throughout: Shapefiles written with Fiona, real KML documents, and
a database dump written by the first release for the migration tests. Measured values are compared with
independent references rather than with the code under test:

- PROJ's analytic projection scale factors;
- Karney's geodesics (`pyproj.Geod`);
- published constants such as the WGS84 meridian arc.

Failure paths are tested by injecting faults: database errors, a process crash between building and
committing results, corrupted archives, and non-finite numbers. CI (GitHub Actions) runs Ruff, the tests
and a Docker smoke test with the sample files on pushes and pull requests. A separate browser job
runs Chromium against an isolated uvicorn and temporary database. It verifies upload, both formats,
invalid inputs, filtering, map selection/limits, complete downloads, deletion, restart persistence,
keyboard access, mobile layout, refresh and Back/Forward navigation.
Compatibility snapshots were captured from the audited pre-UI backend separately on macOS/arm64
and Linux/amd64, because PROJ's final decimal digits differ between platforms; comparisons stay
byte-exact after normalising UUIDs and timestamps.

## Learnings

- **Libraries can fail quietly.** GDAL reports some corruption through its error handler instead of an
  exception. A truncated `.shp` record came back as an empty geometry, and a truncated `.dbf` silently
  ended iteration early. The reader now treats GDAL errors as failures.
- **Numeric behaviour can differ by platform.** Casting an out-of-range float to int64 wraps to a negative
  number on x86-64 but saturates on arm64. The same code bypassed a limit on one platform and not the
  other, so CI runs on the deployment architecture.
- **A projection's inverse accepts more than it should.** PROJ returns a longitude/latitude even for
  points no forward projection could produce, such as a conic's gap or beyond Mercator's pole. A round
  trip catches those points.
- **Grid area is not ground area.** At 13°N, a 1 km × 1 km UTM 43N square covers about 1,000,800 m² on
  the zone's central meridian and about 998,200 m² near its edge.
- **Transactions are not automatic.** Python's `sqlite3` runs DDL outside transactions by default, so a
  failed migration could leave a half-added column. Explicit `BEGIN` fixed it.

## Future scope

- Background processing with a durable queue, and object storage for uploads.
- PostgreSQL/PostGIS for multiple processes and spatial queries.
- Authentication and per-user ownership of uploads; rate limiting.
- KMZ, GeoJSON and GeoPackage input; several Shapefiles per ZIP.
- Antimeridian unwrapping and polar support.
- Optional output in a caller-chosen CRS, and an explicit, opt-in geometry repair mode.
