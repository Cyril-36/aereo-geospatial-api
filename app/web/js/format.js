// Display formatting, status vocabulary and error guidance. Values from the API are never
// changed here: the table may round for display, the details panel shows the full value.

const MIB = 1024 * 1024;

function fixed(value, digits) {
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export const AREA_UNITS = { m2: { label: "m²", factor: 1, digits: 2 }, ha: { label: "ha", factor: 1e4, digits: 4 } };
export const LENGTH_UNITS = { m: { label: "m", factor: 1, digits: 2 }, km: { label: "km", factor: 1e3, digits: 3 } };

export function area(value, unit = "m2") {
  if (value === null || value === undefined) return null;
  const u = AREA_UNITS[unit] || AREA_UNITS.m2;
  return fixed(value / u.factor, u.digits);
}

export function length(value, unit = "m") {
  if (value === null || value === undefined) return null;
  const u = LENGTH_UNITS[unit] || LENGTH_UNITS.m;
  return fixed(value / u.factor, u.digits);
}

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < MIB) return `${fixed(n / 1024, 1)} KB`;
  return `${fixed(n / MIB, 1)} MB`;
}

export function limit(n) {
  return n % MIB === 0 ? `${n / MIB} MiB` : bytes(n);
}

export function when(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return `${date.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  })} UTC`;
}

export function duration(startIso, endIso) {
  if (!startIso || !endIso) return null;
  const ms = Math.max(0, new Date(endIso) - new Date(startIso));
  return ms < 1000 ? `${Math.max(1, Math.round(ms))} ms` : `${fixed(ms / 1000, 1)} s`;
}

export function formatName(format) {
  return format === "KML" ? "KML document" : format === "SHAPEFILE" ? "Zipped Shapefile" : format;
}

const STATUS = {
  MEASURED: ["Measured", "ok"],
  NOT_APPLICABLE: ["Not applicable", "na"],
  UNKNOWN_CRS: ["CRS unknown", "warn"],
  CRS_UNSUPPORTED: ["CRS unsupported", "warn"],
  EMPTY_GEOMETRY: ["No geometry", "warn"],
  INVALID_GEOMETRY: ["Invalid geometry", "warn"],
  UNSUPPORTED_GEOMETRY: ["Unsupported geometry", "warn"],
  UNSUPPORTED_EXTENT: ["Unsupported extent", "warn"],
  TRANSFORM_FAILED: ["Conversion failed", "warn"],
};

export function statusLabel(code) {
  return code ? (STATUS[code] || [code])[0] : "Not measured by this version";
}

export function statusTone(code) {
  return (STATUS[code] || [null, "warn"])[1];
}

export const AREA_TYPES = new Set(["Polygon", "MultiPolygon"]);
export const LENGTH_TYPES = new Set(["LineString", "MultiLineString"]);

// What to show in a measurement cell that has no value: never a misleading zero.
export function missingMeasurement(feature, kind) {
  const applies = kind === "area" ? AREA_TYPES.has(feature.geometry_type) : LENGTH_TYPES.has(feature.geometry_type);
  return applies && feature.status !== "MEASURED" ? "Not measured" : "n/a";
}

const EXPLAIN = {
  POINT_GEOMETRY: "Points have no area or length, so there is nothing to measure.",
  CRS_MISSING:
    "The file doesn’t say which coordinate system its coordinates use, so they can’t be measured. Upload it again with a source coordinate system.",
  CRS_UNPARSEABLE: "The file’s coordinate system couldn’t be read, so nothing was measured.",
  CRS_INVALID: "The file’s coordinate system couldn’t be read, so nothing was measured.",
  CRS_NOT_HORIZONTAL: "The coordinate system isn’t a horizontal (map) system, so areas and lengths can’t be measured in it.",
  NO_TRANSFORMATION: "There is no available conversion from this coordinate system to WGS 84.",
  RING_NOT_CLOSED:
    "A ring of this polygon isn’t closed. It wasn’t measured, and it wasn’t repaired either, because a repair would change the shape.",
  RING_TOO_SHORT: "A ring of this polygon has too few points to enclose an area.",
  POLYGON_WITHOUT_RINGS: "The polygon has no rings, so there is no area to measure.",
  LINE_TOO_SHORT: "The line has fewer than two points, so it has no length.",
  INVALID_GEOMETRY: "The polygon is invalid (for example, it crosses itself). It wasn’t measured or repaired.",
  INVALID_AFTER_DENSIFICATION:
    "The polygon becomes invalid once its edges follow the curved Earth, so it wasn’t measured.",
  INVALID_COORDINATES: "Some coordinates aren’t valid numbers or positions.",
  COORDINATES_OUT_OF_RANGE: "Some coordinates are outside the valid range for latitude and longitude.",
  NON_FINITE_COORDINATES: "Some coordinates aren’t finite numbers.",
  UNSUPPORTED_TYPE: "This geometry type isn’t measured. Only polygons and lines are.",
  UNSUPPORTED_GEOMETRY: "This geometry type isn’t supported, so it has no measurement.",
  NO_GEOMETRY: "The feature has attributes but no shape.",
  EMPTY_GEOMETRY: "The feature’s geometry is empty.",
  ANTIMERIDIAN_CROSSING: "The feature crosses the 180° meridian, which isn’t supported.",
  HEMISPHERE_SCALE: "The feature is too large (about a hemisphere) to measure reliably.",
  OUTSIDE_PROJECTION_DOMAIN: "The feature lies outside the area where its coordinate system is valid.",
  TRANSFORM_ERROR: "Converting the coordinates to WGS 84 failed.",
  DENSIFICATION_LIMIT: "The feature would need too many extra points to follow the Earth’s curve.",
  LAEA_PROJECTION_FAILED: "Projecting the feature for measurement failed.",
  NON_FINITE_MEASUREMENT: "The calculation produced an invalid number, so no value is reported.",
  NEGATIVE_MEASUREMENT: "The calculation produced a negative value, so no value is reported.",
};

export function explain(feature) {
  if (feature.status === "MEASURED") {
    return "Measured on a projection centred on this feature and checked against the WGS 84 ellipsoid.";
  }
  if (!feature.status) return "This feature was stored by an earlier version and was never measured.";
  return EXPLAIN[feature.reason_code] || feature.reason || "This feature wasn’t measured.";
}

const UPLOAD_AGAIN = "Upload a corrected file.";

export const ERROR_GUIDE = {
  UPLOAD_TOO_LARGE: { title: "The file is larger than the server allows", advice: "Reduce the file or split it into smaller uploads." },
  EXPANDED_SIZE_EXCEEDED: { title: "The archive expands to more than the server allows", advice: "Split the data into smaller archives." },
  TOO_MANY_ARCHIVE_MEMBERS: { title: "The archive contains too many files", advice: "Include only the Shapefile’s own files (.shp, .shx, .dbf, .prj, .cpg)." },
  TOO_MANY_FEATURES: { title: "The file has more features than the server allows", advice: "Split it into smaller files." },
  TOO_MANY_VERTICES: { title: "The file has more points than the server allows", advice: "Simplify or split the geometry." },
  UNSUPPORTED_FORMAT: { title: "This file type isn’t supported", advice: "Upload a .zip containing one Shapefile, or a .kml file." },
  KMZ_UNSUPPORTED: { title: "KMZ isn’t supported yet", advice: "Unzip the .kmz and upload the .kml file inside it." },
  EMPTY_FILE: { title: "The file is empty", advice: "Check that you chose the right file." },
  INVALID_ZIP: { title: "The archive couldn’t be read", advice: "The ZIP is damaged or incomplete. Zip the original Shapefile files again and upload the new archive." },
  UNSAFE_ARCHIVE_PATH: { title: "The archive contains unsafe paths", advice: "An entry points outside the archive folder. Re-create the ZIP from the original files." },
  ARCHIVE_SYMLINK: { title: "The archive contains a link", advice: "Re-create the ZIP from the original files, without symbolic links." },
  ENCRYPTED_ARCHIVE_ENTRY: { title: "The archive is password-protected", advice: "Upload an archive without encryption." },
  DUPLICATE_ARCHIVE_PATH: { title: "The archive contains the same path twice", advice: "Re-create the ZIP from the original files." },
  NESTED_ARCHIVE: { title: "The archive contains another archive", advice: "Upload the inner Shapefile’s files directly in one ZIP." },
  NO_SHAPEFILE: { title: "The ZIP doesn’t contain a Shapefile", advice: "It needs a .shp with its matching .shx and .dbf." },
  MULTIPLE_DATASETS_UNSUPPORTED: { title: "The ZIP contains more than one Shapefile", advice: "Upload each Shapefile in its own ZIP." },
  MISSING_SHAPEFILE_COMPONENT: { title: "The Shapefile is incomplete", advice: "Add the missing part: a Shapefile needs .shp, .shx and .dbf with the same name." },
  MALFORMED_DATASET: { title: "The Shapefile couldn’t be read", advice: "Its contents are damaged or inconsistent. Export it again from the source program." },
  INVALID_KML: { title: "The KML couldn’t be parsed", advice: "The file isn’t well-formed KML. Export it again from the source program." },
  UNSAFE_XML: { title: "The KML uses XML features that are refused for safety", advice: "Remove entity declarations (DTDs) from the file and upload it again." },
  INVALID_SOURCE_CRS: { title: "The coordinate system couldn’t be read", advice: "Use an EPSG code such as EPSG:32643, or WKT text." },
  CRS_CONFLICT: { title: "The coordinate system you supplied doesn’t match the file", advice: "The file already declares one. Upload again without a source coordinate system, or with the same one." },
  INTERRUPTED: { title: "Processing was interrupted", advice: "The server restarted while this file was being processed. Upload it again." },
  INTERNAL_ERROR: { title: "The server hit an unexpected error", advice: "Try again. If it keeps happening, report the request id shown here." },
  INCONSISTENT_RESULT: { title: "The results failed a consistency check", advice: "Nothing was stored. Upload the file again; if it repeats, report it." },
  PERSISTENCE_FAILED: { title: "The results couldn’t be saved", advice: "Nothing was stored. Try the upload again." },
  FILE_NOT_FOUND: { title: "No file with this ID", advice: "It may have been deleted, or the link may be incomplete." },
  FILE_FAILED: { title: "This file has no measurements", advice: "Processing failed, so no features were stored." },
  FILE_NOT_READY: { title: "The file is still being processed", advice: "Wait a moment and reload." },
  FEATURE_NOT_FOUND: { title: "That feature isn’t in this file", advice: "The link may point to a feature that doesn’t exist." },
  MEASUREMENTS_UNAVAILABLE: { title: "This file was never measured", advice: "It was stored by an earlier version. Upload the original file again to measure it." },
  FORM_LIMIT_EXCEEDED: { title: "The upload form was too large", advice: "Send one file and, optionally, one coordinate system." },
  MALFORMED_REQUEST: { title: "The upload didn’t arrive intact", advice: "Try again. If it keeps happening, check any proxy between you and the server." },
  DUPLICATE_FORM_FIELD: { title: "The upload form repeated a field", advice: "Send one file and at most one coordinate system." },
  VALIDATION_ERROR: { title: "The request was incomplete", advice: "Choose a file and try again." },
};

export function errorGuide(code) {
  return ERROR_GUIDE[code] || { title: "The file couldn’t be processed", advice: UPLOAD_AGAIN };
}

const ORIGIN = {
  PRJ_FILE: "from the file’s .prj",
  KML_SPECIFICATION: "KML always uses WGS 84",
  OVERRIDE: "supplied at upload",
  NONE: "the file doesn’t declare one",
};

export function crsOrigin(origin) {
  return ORIGIN[origin] || "";
}
