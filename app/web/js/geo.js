// Decides whether a feature can be drawn on the latitude/longitude map, before it reaches
// Leaflet. Only geometry the API labels EPSG:4326 is ever plotted; projected or unknown
// source coordinates are never read as degrees.
const DRAWABLE = new Set([
  "Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon", "GeometryCollection",
]);

function isPosition(p) {
  return (
    Array.isArray(p) && p.length >= 2 &&
    typeof p[0] === "number" && typeof p[1] === "number" &&
    Number.isFinite(p[0]) && Number.isFinite(p[1]) &&
    p[0] >= -180 && p[0] <= 180 && p[1] >= -90 && p[1] <= 90
  );
}

function positions(list, minimum) {
  if (!Array.isArray(list) || list.length < minimum) return "unsupported shape";
  return list.every(isPosition) ? list.length : "coordinates out of range";
}

function sum(parts, check) {
  if (!Array.isArray(parts) || parts.length === 0) return "unsupported shape";
  let total = 0;
  for (const part of parts) {
    const result = check(part);
    if (typeof result === "string") return result;
    total += result;
  }
  return total;
}

// Returns the vertex count, or the reason the geometry can't be drawn safely.
function walk(g) {
  if (!g || typeof g !== "object" || !DRAWABLE.has(g.type)) return "unsupported shape";
  const c = g.coordinates;
  switch (g.type) {
    case "Point":
      return isPosition(c) ? 1 : "coordinates out of range";
    case "MultiPoint":
      return positions(c, 1);
    case "LineString":
      return positions(c, 2);
    case "MultiLineString":
      return sum(c, (line) => positions(line, 2));
    case "Polygon":
      return sum(c, (ring) => positions(ring, 4));
    case "MultiPolygon":
      return sum(c, (polygon) => sum(polygon, (ring) => positions(ring, 4)));
    case "GeometryCollection":
      return sum(g.geometries, walk);
    default:
      return "unsupported shape";
  }
}

export function plotCheck(feature) {
  if (!feature.geometry) return { ok: false, reason: "no geometry" };
  if (feature.geometry_crs !== "EPSG:4326") {
    return {
      ok: false,
      reason: feature.geometry_crs
        ? `coordinates are in ${feature.geometry_crs}, not latitude/longitude`
        : "coordinate system unknown",
    };
  }
  const result = walk(feature.geometry);
  return typeof result === "string" ? { ok: false, reason: result } : { ok: true, vertices: result };
}
