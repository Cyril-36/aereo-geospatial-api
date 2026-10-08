// Map preview (Leaflet). It draws the features matching the table's filters, page by page,
// up to configured feature and vertex limits, and says plainly when the preview is partial.
// It is only a preview: every number shown elsewhere comes from the API, not from here.
import { api } from "./api.js";
import { h, clear } from "./dom.js";
import { featureName } from "./format.js";
import { plotCheck } from "./geo.js";
import { nextGeneration, isAbort } from "./state.js";

const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const COLORS = { measured: "#1d4e89", attention: "#a84b0f", point: "#1d4e89", selected: "#16201a" };

function styleFor(feature, selected) {
  const attention = feature.status !== "MEASURED" && feature.status !== "NOT_APPLICABLE";
  const color = selected ? COLORS.selected : attention ? COLORS.attention : COLORS.measured;
  return {
    color,
    weight: selected ? 3.5 : 2,
    opacity: 1,
    fillColor: selected ? COLORS.measured : color,
    fillOpacity: selected ? 0.42 : attention ? 0.1 : 0.2,
    dashArray: attention && !selected ? "5 3" : null,
  };
}

export function mountMap(region, { fileId, config, onPick }) {
  const baseFeatures = config.map_max_features;
  const baseVertices = config.map_max_vertices;
  const mapEl = h("div", { id: "map", role: "region", "aria-label": "Map preview. Use the table to select features with the keyboard." });
  const status = h("p", { id: "map-status", class: "map-status", role: "status", style: "margin:0" });
  const more = h("div");
  const skipsBox = h("div");
  const fitButton = h("button", { type: "button", class: "btn btn-sm", onclick: () => fit() }, "Fit to all features");
  const fallback = h("div");
  const body = h(
    "div",
    { style: "display:flex;flex-direction:column;gap:8px" },
    mapEl,
    status,
    more,
    skipsBox,
    h(
      "div",
      { class: "legend", "aria-hidden": "true" },
      h("span", {}, h("i", { style: "background:rgba(29,78,137,.2);border:1px solid #1d4e89" }), "measured"),
      h("span", {}, h("i", { style: "border:1px dashed #a84b0f" }), "needs attention"),
      h("span", {}, h("i", { style: "background:rgba(29,78,137,.42);border:2px solid #16201a" }), "selected"),
    ),
    h("p", { class: "small", style: "margin:0" }, "A preview drawn from the WGS 84 geometry the API returns. Measurements always come from the API, never from this drawing."),
  );
  region.append(
    h(
      "section",
      { class: "map-panel", "aria-labelledby": "map-title" },
      h("div", { class: "map-head" }, h("h2", { id: "map-title", class: "section-title", style: "margin:0" }, "Map preview"), fitButton),
      body,
      fallback,
    ),
  );

  let map = null;
  let group = null;
  const layers = new Map(); // feature index -> {layer, feature}
  let selectedIndex = null;
  let run = null; // the current load: {gen, params, offset, matching, drawn, vertices, maxF, maxV, skips, partial, done}

  function ensureMap() {
    if (map) return true;
    if (!window.L) return false;
    map = window.L.map(mapEl, { preferCanvas: true, zoomSnap: 0.25, worldCopyJump: false });
    if (config.map_tile_url) {
      window.L.tileLayer(config.map_tile_url, { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(map);
    }
    group = window.L.featureGroup().addTo(map);
    map.setView([0, 0], 2);
    return true;
  }

  function showFailure() {
    body.hidden = true;
    fitButton.disabled = true;
    clear(fallback).append(
      h(
        "div",
        { class: "map-fallback", role: "status" },
        "The map preview couldn’t load. The measurements, the table and downloads still work. ",
        h("button", { type: "button", class: "link-btn", onclick: () => load(run ? run.params : {}) }, "Try loading the map again"),
      ),
    );
  }

  function renderStatus() {
    const r = run;
    clear(more);
    clear(skipsBox);
    if (r.drawn === 0 && r.done && !r.partial) {
      status.textContent =
        r.matching === 0
          ? "No features match the current filters."
          : "No feature can be placed on the map: none has coordinates that can be read as latitude and longitude.";
    } else if (r.partial) {
      status.textContent = `Map preview is partial: showing ${r.drawn} of ${r.matching} matching features (limit reached). The table and downloads include all of them.`;
      if (r.maxF < 2 * baseFeatures && r.maxV < 2 * baseVertices) {
        more.append(h("button", { type: "button", class: "btn btn-sm", onclick: () => loadMore() }, "Load more"));
      }
    } else {
      status.textContent = r.done ? `${r.drawn} of ${r.matching} matching features on the map.` : `Drawing the map: ${r.drawn} of ${r.matching} features so far…`;
    }
    const skipped = [...r.skips.values()].reduce((n, list) => n + list.length, 0);
    if (skipped) {
      skipsBox.append(
        h(
          "details",
          { id: "map-skips", class: "skips", style: "font-size:13px" },
          h("summary", {}, `${skipped} not shown: why?`),
          h(
            "ul",
            { style: "margin:6px 0 0;padding-left:18px" },
            [...r.skips.entries()].map(([reason, names]) =>
              h("li", {}, h("strong", {}, reason), `: ${names.slice(0, 20).join(", ")}${names.length > 20 ? `, and ${names.length - 20} more` : ""}`),
            ),
          ),
        ),
      );
    }
    fitButton.disabled = r.drawn === 0;
  }

  function add(feature) {
    const check = plotCheck(feature);
    if (!check.ok) {
      const list = run.skips.get(check.reason) || [];
      list.push(featureName(feature));
      run.skips.set(check.reason, list);
      return true;
    }
    if (run.drawn + 1 > run.maxF || run.vertices + check.vertices > run.maxV) {
      run.partial = true;
      return false;
    }
    try {
      const layer = window.L.geoJSON(feature.geometry, {
        style: () => styleFor(feature, feature.index === selectedIndex),
        pointToLayer: (_, latlng) => window.L.circleMarker(latlng, { radius: 5 }),
      });
      layer.on("click", () => onPick(feature.index));
      layer.addTo(group);
      layers.set(feature.index, { layer, feature });
      run.drawn += 1;
      run.vertices += check.vertices;
    } catch {
      const list = run.skips.get("couldn’t be drawn") || [];
      list.push(featureName(feature));
      run.skips.set("couldn’t be drawn", list);
    }
    return true;
  }

  async function continueLoading() {
    const r = run;
    if (r.loading) return;
    r.loading = true;
    try {
      while (r.offset !== null && !r.partial) {
        const remaining = r.maxF - r.seen;
        if (remaining <= 0) { r.partial = true; renderStatus(); break; }
        const page = await api.getMeasurements(fileId, { ...r.params, limit: Math.min(config.max_page_size, remaining), offset: r.offset }, { signal: r.gen.signal });
        if (!r.gen.isCurrent()) return;
        r.matching = page.pagination.total;
        let consumed = 0;
        for (const feature of page.features) {
          if (!add(feature)) break;
          consumed += 1;
          r.seen += 1;
        }
        r.offset = r.partial ? r.offset + consumed : page.pagination.next_offset;
        if (r.seen >= r.maxF && r.offset !== null) r.partial = true;
        if (!r.fitted && r.drawn) {
          fit();
          r.fitted = true;
        }
        if (r.offset === null) r.done = true;
        renderStatus();
      }
      if (selectedIndex !== null) highlight(selectedIndex, false);
    } catch (error) {
      if (isAbort(error) || !r.gen.isCurrent()) return;
      showFailure();
    } finally {
      r.loading = false;
    }
  }

  function load(params) {
    const gen = nextGeneration("map");
    body.hidden = false;
    clear(fallback);
    if (!ensureMap()) {
      run = { params };
      showFailure();
      return;
    }
    group.clearLayers();
    layers.clear();
    run = {
      gen, params, offset: 0, matching: 0, drawn: 0, vertices: 0, seen: 0,
      maxF: baseFeatures, maxV: baseVertices, skips: new Map(), partial: false, done: false, fitted: false,
    };
    status.textContent = "Loading the map…";
    setTimeout(() => map && map.invalidateSize(), 0);
    continueLoading();
  }

  function loadMore() {
    if (run.loading || run.maxF >= 2 * baseFeatures || run.maxV >= 2 * baseVertices) return;
    run.maxF += baseFeatures;
    run.maxV += baseVertices;
    run.partial = false;
    renderStatus();
    continueLoading();
  }

  function fit() {
    if (!map || !group) return;
    const bounds = group.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 18 });
  }

  function highlight(index, move) {
    for (const [i, entry] of layers) entry.layer.setStyle(styleFor(entry.feature, i === index));
    const entry = layers.get(index);
    if (!entry) return;
    entry.layer.bringToFront();
    if (move) {
      const bounds = entry.layer.getBounds();
      if (bounds.isValid()) map.flyToBounds(bounds, { padding: [48, 48], maxZoom: 18, duration: 0.4 });
    }
  }

  if (window.__aereoTest) {
    window.__aereoMap = {
      pick: (index) => onPick(index),
      drawn: () => [...layers.keys()].sort((a, b) => a - b),
      selected: () => selectedIndex,
    };
  }

  return {
    load,
    fit,
    select(index) {
      selectedIndex = index;
      if (map) highlight(index, index !== null);
    },
    destroy() {
      if (map) map.remove();
      map = null;
    },
  };
}
