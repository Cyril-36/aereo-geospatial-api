// Feature details: the measurement first, then status, warnings, attributes, and the
// technical record (CRS, projection, transformation, geodesic check) behind a disclosure.
import { h, clear, icon } from "./dom.js";
import * as fmt from "./format.js";

function measurementBlock(feature, units) {
  if (feature.area_m2 !== null) {
    return {
      label: "Area",
      text: `${fmt.area(feature.area_m2, units.au)} ${fmt.AREA_UNITS[units.au].label}`,
      full: `${String(feature.area_m2)} m²`,
    };
  }
  if (feature.length_m !== null) {
    return {
      label: "Length",
      text: `${fmt.length(feature.length_m, units.lu)} ${fmt.LENGTH_UNITS[units.lu].label}`,
      full: `${String(feature.length_m)} m`,
    };
  }
  const label = fmt.AREA_TYPES.has(feature.geometry_type) ? "Area" : fmt.LENGTH_TYPES.has(feature.geometry_type) ? "Length" : "Measurement";
  const kind = label === "Length" ? "length" : "area";
  const text = feature.status === "NOT_APPLICABLE" ? "Not applicable" : fmt.missingMeasurement(feature, kind) === "n/a" ? "Not applicable" : "Not measured";
  return { label, text, full: null };
}

function technical(feature) {
  const t = feature.transformation;
  const g = feature.geodesic_reference;
  const rows = [
    ["Source CRS", feature.source_crs || "Unknown"],
    [
      "Map geometry",
      feature.geometry_origin === "TRANSFORMED"
        ? "Converted to WGS 84 (EPSG:4326)"
        : feature.geometry_origin === "SOURCE"
          ? `Original coordinates in ${feature.geometry_crs || "an unknown coordinate system"}`
          : "No geometry",
    ],
    [
      "Method",
      feature.measurement_method === "LOCAL_LAEA"
        ? "LOCAL_LAEA: equal-area projection centred on the feature, edges densified to follow the Earth"
        : feature.measurement_method || "Not measured",
    ],
    ["Projection", feature.measurement_crs || "None"],
    [
      "Transformation",
      t ? `${t.operation} · accuracy ${t.accuracy_m === null ? "not stated" : `${t.accuracy_m} m`}${t.best_available ? "" : " · not the best available"}` : "None",
    ],
    [
      "Geodesic check",
      g
        ? `Reference ${g.area_m2 !== undefined && g.area_m2 !== null ? `${g.area_m2} m²` : `${g.length_m} m`}; relative difference ${g.relative_difference === null ? "n/a" : g.relative_difference.toExponential(1)} (a warning is raised above 1e-3)`
        : "Not applicable",
    ],
    ["Points added", String(feature.generated_vertices ?? 0)],
  ];
  return h(
    "details",
    { class: "tech" },
    h("summary", {}, "Technical details"),
    h("dl", { class: "kv", style: "margin-top:8px" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", { class: k === "Projection" ? "mono" : "" }, v)])),
  );
}

export function renderDetails(panel, feature, { units, onClose }) {
  clear(panel);
  panel.hidden = false;
  const m = measurementBlock(feature, units);
  const tone = fmt.statusTone(feature.status);
  const props = Object.entries(feature.properties || {});
  panel.append(
    h(
      "div",
      { class: "details-head" },
      h(
        "div",
        { style: "min-width:0" },
        h("h2", { id: "details-title", tabindex: "-1" }, fmt.featureName(feature)),
        h("div", { class: "row-sub" }, `#${feature.index} · source ID ${feature.source_id ?? "none"} · ${feature.geometry_type || "no geometry"}`),
      ),
      h(
        "button",
        { type: "button", class: "btn btn-sm", "aria-label": "Close feature details", onclick: () => onClose() },
        icon(["M5 5l14 14", "M19 5L5 19"], 14),
      ),
    ),
    h(
      "div",
      {},
      h("div", { class: "small" }, m.label),
      h("div", { class: "measure", style: m.full ? "" : "color:var(--faint)" }, m.text),
      m.full && h("div", { class: "row-sub", style: "overflow-wrap:anywhere" }, `Full value from the API: ${m.full}`),
    ),
    h(
      "div",
      { style: "display:flex;flex-direction:column;gap:4px" },
      h("span", { class: `badge tone-${tone}`, style: "align-self:flex-start" }, fmt.statusLabel(feature.status)),
      h("p", { style: "margin:0;font-size:14px" }, fmt.explain(feature)),
      feature.status !== "MEASURED" && feature.reason_code
        ? h("p", { class: "code-line", style: "margin:0" }, `${feature.reason_code}: ${feature.reason || ""}`)
        : null,
    ),
    (feature.warnings || []).map((w) =>
      h("div", { style: "font-size:13px;padding:8px 10px;background:var(--warn-soft);border-radius:6px" }, h("span", { class: "mono", style: "font-weight:500;color:var(--warn-strong)" }, w.code), ` · ${w.message}`),
    ),
    feature.folder_path && feature.folder_path.length
      ? h("div", { style: "font-size:13px" }, h("span", { class: "small" }, "KML folder "), feature.folder_path.join(" / "))
      : null,
    h(
      "div",
      {},
      h("h3", { style: "margin:0 0 4px;font-size:14px" }, "Attributes"),
      props.length
        ? h("dl", { class: "kv" }, props.map(([k, v]) => [h("dt", { class: "mono" }, k), h("dd", {}, v === null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v))]))
        : h("p", { class: "small", style: "margin:0" }, "No attributes."),
    ),
    technical(feature),
  );
}
