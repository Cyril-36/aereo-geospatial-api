// Results view for one file: overview, then the feature table, map and details (mounted into
// #table-region and #map-region). Everything the page needs comes from the API.
import { api, ApiError } from "./api.js";
import { h, clear, toast } from "./dom.js";
import * as fmt from "./format.js";
import { isNavigation, navigate, queryParams, updateQuery } from "./router.js";
import { nextGeneration, isAbort } from "./state.js";
import { prepareReupload } from "./upload.js";
import { mountTable } from "./table.js";
import { renderDetails } from "./details.js";

let configCache = null;

export async function loadConfig(signal) {
  if (!configCache) configCache = await api.getConfig({ signal });
  return configCache;
}

export function renderMissing(root, id) {
  document.title = "No file with this ID · Survey measurement workspace";
  clear(root).append(
    h(
      "section",
      { style: "max-width:640px;padding:32px 0;display:flex;flex-direction:column;gap:12px" },
      h("h1", { class: "page-title" }, "No file with this ID"),
      h("p", { style: "margin:0" }, h("code", { style: "overflow-wrap:anywhere" }, id)),
      h(
        "p",
        { class: "lede" },
        "It may have been deleted, or the link may be incomplete. File IDs look like ",
        h("span", { class: "mono" }, "84626f5c-4143-4746-8966-4077ea7fb3a9"),
        ".",
      ),
      h(
        "div",
        { class: "btn-row" },
        h("a", { href: "/history", "data-link": true, class: "btn btn-primary" }, "Go to file history"),
        h("a", { href: "/", "data-link": true, class: "btn" }, "Upload a file"),
      ),
    ),
  );
}

export function renderNotFound(root) {
  document.title = "Page not found · Survey measurement workspace";
  root.append(
    h("h1", { class: "page-title" }, "Page not found"),
    h("p", { class: "lede" }, "There’s nothing at this address."),
    h(
      "div",
      { class: "btn-row", style: "margin-top:12px" },
      h("a", { href: "/", "data-link": true, class: "btn btn-primary" }, "Upload a file"),
      h("a", { href: "/history", "data-link": true, class: "btn" }, "Go to file history"),
    ),
  );
}

function loading(root) {
  root.append(
    h("h1", { class: "vh" }, "Loading results"),
    h(
      "div",
      { "aria-hidden": "true", style: "display:flex;flex-direction:column;gap:12px" },
      h("div", { class: "skeleton", style: "height:28px;width:40%" }),
      h("div", { class: "tiles" }, [1, 2, 3, 4].map(() => h("div", { class: "skeleton", style: "height:64px" }))),
      h("div", { class: "skeleton", style: "height:240px" }),
    ),
  );
}

function copy(text, label) {
  const fail = () => toast(`Couldn’t copy automatically. Select the ${label.toLowerCase()} to copy it.`);
  try {
    navigator.clipboard.writeText(text).then(() => toast(`${label} copied`), fail);
  } catch {
    fail();
  }
}

function outcomeLine(info, counts) {
  const { total, measured, na, attention } = counts;
  if (total === 0) return "Processing completed, but the file contains no features.";
  if (info.crs_status === "UNKNOWN" && measured === 0) {
    return "Processing completed, but nothing could be measured: the coordinate system is unknown.";
  }
  let line = `Processing completed: ${measured} of ${total} features measured`;
  if (na) line += `, ${na} ${na === 1 ? "point" : "points"} with nothing to measure`;
  line += attention ? `, ${attention} ${attention === 1 ? "needs" : "need"} attention.` : ". Nothing needs attention.";
  return line;
}

export function countsOf(info) {
  const counts = info.counts || {};
  const total = info.feature_count || 0;
  const measured = counts.MEASURED || 0;
  const na = counts.NOT_APPLICABLE || 0;
  return { total, measured, na, attention: total - measured - na };
}

function failPanel(info) {
  const guide = fmt.errorGuide(info.error ? info.error.code : "");
  return h(
    "div",
    { id: "fail-panel", class: "notice-warn", role: "alert", style: "max-width:760px" },
    h("h2", { style: "margin:0;font-size:18px;color:var(--warn-strong)" }, guide.title),
    h("p", { style: "margin:0" }, guide.advice),
    info.error && h("p", { class: "code-line", style: "margin:0" }, `${info.error.code}: ${info.error.message}`),
    h("p", { class: "small", style: "margin:0" }, "No features were stored for this file, so there is nothing to measure or download."),
    h("div", {}, h("a", { href: "/", "data-link": true, class: "btn btn-primary" }, "Upload a corrected file")),
  );
}

async function sums(fileId, config, signal) {
  let area = 0;
  let length = 0;
  let areas = 0;
  let lengths = 0;
  let offset = 0;
  while (offset !== null) {
    const page = await api.getMeasurements(fileId, { status: "measured", limit: config.max_page_size, offset }, { signal });
    for (const f of page.features) {
      if (f.area_m2 !== null) {
        area += f.area_m2;
        areas += 1;
      }
      if (f.length_m !== null) {
        length += f.length_m;
        lengths += 1;
      }
    }
    offset = page.pagination.next_offset;
  }
  return { area, length, areas, lengths };
}

export function renderResults(root, { id }) {
  document.title = "Results · Survey measurement workspace";
  const gen = nextGeneration("results");
  const cleanups = [];
  const focusOnLoad = isNavigation();
  loading(root);

  (async () => {
    let config;
    let info;
    try {
      [config, info] = await Promise.all([loadConfig(gen.signal), api.getFile(id, { signal: gen.signal })]);
    } catch (error) {
      if (isAbort(error) || !gen.isCurrent()) return;
      clear(root);
      if (error instanceof ApiError && error.code === "FILE_NOT_FOUND") {
        renderMissing(root, id);
      } else {
        root.append(
          h("h1", { class: "page-title" }, "Couldn’t load this result"),
          h("p", { class: "lede" }, "The server didn’t respond. Nothing has changed; try again."),
          h("div", { style: "margin-top:12px" }, h("button", { type: "button", class: "btn btn-primary", onclick: () => navigate(location.pathname + location.search, { replace: true }) }, "Try again")),
        );
      }
      if (focusOnLoad) focusFirstHeading(root);
      return;
    }
    if (!gen.isCurrent()) return;
    clear(root);
    document.title = `${info.filename} · Survey measurement workspace`;
    const view = buildOverview(info, config, gen, cleanups);
    root.append(view);
    if (focusOnLoad) focusFirstHeading(root);
  })();

  return () => {
    for (const fn of cleanups) fn();
  };
}

function focusFirstHeading(root) {
  const heading = root.querySelector("h1");
  if (!heading) return;
  heading.setAttribute("tabindex", "-1");
  heading.focus();
}

function buildOverview(info, config, gen, cleanups) {
  const counts = countsOf(info);
  const completed = info.status === "COMPLETED";
  const params = queryParams();
  const areaUnit = params.get("au") === "ha" ? "ha" : "m2";
  const lengthUnit = params.get("lu") === "km" ? "km" : "m";
  const processed = fmt.duration(info.created_at, info.processed_at);
  const meta = [fmt.formatName(info.format), fmt.bytes(info.size_bytes), `uploaded ${fmt.when(info.created_at)}`];
  if (processed) meta.push(`processed in ${processed}`);
  const link = `${location.origin}/files/${info.id}`;

  const actions = h("div", { id: "result-actions", class: "btn-row" }, h("a", { href: "/", "data-link": true, class: "btn" }, "Upload another file"));

  const section = h(
    "section",
    { class: "results", "aria-labelledby": "res-title" },
    h(
      "nav",
      { class: "crumbs", "aria-label": "Breadcrumb" },
      h("a", { href: "/history", "data-link": true }, "File history"),
      h("span", { "aria-hidden": "true", style: "color:var(--faint)" }, " / "),
      h("span", {}, info.filename),
    ),
    h(
      "div",
      { class: "head-row" },
      h("div", { style: "min-width:0" }, h("h1", { id: "res-title", class: "file-title" }, info.filename), h("div", { class: "meta" }, meta.join(" · "))),
      actions,
    ),
    h(
      "div",
      { class: "panel panel-tight id-strip" },
      h(
        "div",
        {},
        h("span", { class: "small" }, "File ID"),
        h("code", { id: "file-id", style: "font-size:13px;overflow-wrap:anywhere" }, info.id),
        h("button", { type: "button", class: "btn btn-xs", onclick: () => copy(info.id, "File ID") }, "Copy ID"),
      ),
      h(
        "div",
        {},
        h("span", { class: "small" }, "Link"),
        h("code", { style: "font-size:13px;overflow-wrap:anywhere" }, `/files/${info.id}`),
        h("button", { type: "button", class: "btn btn-xs", onclick: () => copy(link, "Link") }, "Copy link"),
      ),
    ),
  );

  if (info.status === "FAILED") {
    section.append(failPanel(info));
    return section;
  }
  if (!completed) {
    const guide = fmt.errorGuide(info.status === "PROCESSING" ? "FILE_NOT_READY" : "MEASUREMENTS_UNAVAILABLE");
    section.append(h("div", { class: "notice-warn", role: "status" }, h("div", { class: "title" }, guide.title), h("p", { style: "margin:0" }, guide.advice)));
    return section;
  }

  const areaSum = h("span", { class: "mono" }, "Calculating…");
  const lengthSum = h("span", { class: "mono" }, "Calculating…");
  const sumsBox = h("div", { class: "facts" });
  sumsBox.append(
    h("div", { id: "crs-summary" }, h("span", { class: "small" }, "Coordinate system "), h("span", { class: "mono" }, info.crs || "Unknown"), h("span", { class: "small" }, ` · ${fmt.crsOrigin(info.crs_origin)}`)),
  );
  const areaFact = h("div", { id: "area-sum", hidden: true }, h("span", { class: "small" }, "Sum of feature areas "), areaSum);
  const lengthFact = h("div", { id: "length-sum", hidden: true }, h("span", { class: "small" }, "Sum of feature lengths "), lengthSum);
  sumsBox.append(areaFact, lengthFact);
  const overlapNote = h("p", { class: "small", style: "margin:0", hidden: true }, "The area sum adds each measured polygon separately. Overlapping polygons are counted twice, so it isn’t the land footprint.");

  const body = h(
    "div",
    { style: "display:flex;flex-direction:column;gap:14px" },
    h("p", { id: "outcome-line", class: "outcome" }, outcomeLine(info, counts)),
    h(
      "div",
      { class: "tiles" },
      [
        ["Features", counts.total, "var(--ink)"],
        ["Measured", counts.measured, "var(--ok-fg)"],
        ["Points (nothing to measure)", counts.na, "var(--na-fg)"],
        ["Need attention", counts.attention, counts.attention ? "var(--warn-fg)" : "var(--ink)"],
      ].map(([label, value, color]) => h("div", { class: "tile" }, h("div", { class: "small" }, label), h("div", { class: "num", style: `color:${color}` }, String(value)))),
    ),
    sumsBox,
    overlapNote,
    (info.warnings || []).map((w) => h("div", { class: "notice-warn", role: "note", style: "padding:10px 14px;font-size:14px" }, h("div", {}, h("span", { class: "mono", style: "font-weight:500;color:var(--warn-strong)" }, w.code), ` · ${w.message}`))),
  );
  if (info.crs_status === "UNKNOWN" && counts.measured === 0 && counts.total > 0) {
    body.append(
      h(
        "div",
        { id: "missing-crs", class: "notice-warn", style: "flex-direction:row;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px" },
        h(
          "div",
          { style: "max-width:70ch" },
          h("div", { class: "title" }, "Nothing could be measured: the coordinate system is unknown"),
          h("div", { style: "font-size:14px" }, "Measuring needs to know what the coordinates mean. This file doesn’t declare a coordinate system, and nothing is guessed. If you know it, upload the file again and supply it. That creates a new record; this one stays in history."),
        ),
        h("button", { type: "button", class: "btn btn-primary", onclick: () => { prepareReupload(info.filename); navigate("/"); } }, "Upload again with a coordinate system"),
      ),
    );
  }
  const tableRegion = h("div", { id: "table-region", class: "work-table" });
  const sideRegion = h("div", { id: "side-region", class: "work-side" });
  section.append(body, h("div", { class: "work" }, tableRegion, sideRegion));

  let totals = null;
  let units = { au: areaUnit, lu: lengthUnit };
  const showSums = () => {
    if (!totals) return;
    areaSum.textContent = `${fmt.area(totals.area, units.au)} ${fmt.AREA_UNITS[units.au].label}`;
    lengthSum.textContent = `${fmt.length(totals.length, units.lu)} ${fmt.LENGTH_UNITS[units.lu].label}`;
  };
  if (counts.total > 0) {
    cleanups.push(
      mountWorkspace(info, config, tableRegion, sideRegion, (next) => {
        units = next;
        showSums();
      }),
    );
  }

  if (counts.measured > 0) {
    sums(info.id, config, gen.signal)
      .then((s) => {
        if (!gen.isCurrent()) return;
        totals = s;
        showSums();
        if (s.areas) {
          areaFact.append(h("span", { class: "small" }, ` (${s.areas} measured ${s.areas === 1 ? "polygon" : "polygons"})`));
          areaFact.hidden = false;
          overlapNote.hidden = false;
        }
        if (s.lengths) {
          lengthFact.append(h("span", { class: "small" }, ` (${s.lengths} measured ${s.lengths === 1 ? "line" : "lines"})`));
          lengthFact.hidden = false;
        }
      })
      .catch((error) => {
        if (isAbort(error) || !gen.isCurrent()) return;
        areaSum.textContent = "Sums unavailable";
        areaFact.hidden = false;
      });
  }
  return section;
}

// Table, details and (from Task 9) map for a completed file, kept in sync through one
// selected feature index that also lives in the URL as ?feature=.
function mountWorkspace(info, config, tableRegion, sideRegion, onUnits) {
  const detailsPanel = h("aside", { id: "details", class: "details", hidden: true, "aria-labelledby": "details-title" });
  sideRegion.append(detailsPanel);
  let selectedIndex = null;
  let selectedFeature = null;
  const narrow = window.matchMedia("(max-width: 720px)");

  const table = mountTable(tableRegion, {
    fileId: info.id,
    info,
    config,
    onSelect: (feature) => selectFeature(feature, { push: true }),
    onFiltersChanged: () => refreshHidden(),
    onUnitsChanged: (units) => {
      if (selectedFeature) showDetails(selectedFeature);
      onUnits(units);
    },
  });

  function showDetails(feature) {
    renderDetails(detailsPanel, feature, { units: table.units(), onClose: close });
    detailsPanel.classList.toggle("sheet", narrow.matches);
  }

  function selectFeature(feature, { push }) {
    selectedIndex = feature.index;
    selectedFeature = feature;
    table.setSelected(feature.index);
    table.setHiddenNotice(false);
    showDetails(feature);
    updateQuery({ feature: feature.index }, { push });
    if (narrow.matches) detailsPanel.querySelector("h2").focus();
  }

  function close() {
    const index = selectedIndex;
    selectedIndex = null;
    selectedFeature = null;
    detailsPanel.hidden = true;
    clear(detailsPanel);
    table.setSelected(null);
    table.setHiddenNotice(false);
    updateQuery({ feature: "" }, { push: true });
    if (index !== null && !table.focusRow(index)) document.getElementById("feature-table")?.focus();
  }

  async function refreshHidden() {
    if (selectedIndex === null) return;
    const index = selectedIndex;
    try {
      const position = await api.getPosition(info.id, index, { ...table.params(), limit: table.pageSize });
      if (selectedIndex === index) table.setHiddenNotice(!position.matches);
    } catch {
      // the notice is a hint; the table itself is unaffected
    }
  }

  async function featureAt(index) {
    const page = await api.getMeasurements(info.id, { limit: 1, offset: index });
    return page.features[0] || null;
  }

  async function openIndex(index, { push }) {
    try {
      const position = await api.getPosition(info.id, index, { ...table.params(), limit: table.pageSize });
      let feature = null;
      if (position.matches) {
        await table.showOffset(position.page_offset);
        feature = table.rowFor(index);
      }
      feature = feature || (await featureAt(index));
      if (!feature) return;
      selectFeature(feature, { push });
      if (!position.matches) table.setHiddenNotice(true);
    } catch (error) {
      if (error instanceof ApiError && error.code === "FEATURE_NOT_FOUND") updateQuery({ feature: "" });
    }
  }

  const onKey = (event) => {
    if (event.key === "Escape" && selectedIndex !== null && !document.querySelector("dialog[open]")) close();
  };
  document.addEventListener("keydown", onKey);

  (async () => {
    await table.load();
    const raw = queryParams().get("feature");
    if (raw !== null && /^\d+$/.test(raw)) await openIndex(Number(raw), { push: false });
  })();

  return () => {
    document.removeEventListener("keydown", onKey);
    table.destroy();
  };
}
