// Feature table. Filtering, sorting and paging are done by the API across the whole file;
// the table only shows one page. Its state lives in the URL so a refresh or link keeps it.
import { api } from "./api.js";
import { h, clear, debounce } from "./dom.js";
import * as fmt from "./format.js";
import { queryParams, updateQuery } from "./router.js";
import { nextGeneration, isAbort } from "./state.js";

const FILTER_KEYS = ["q", "geometry", "status", "warnings"];

function readState() {
  const p = queryParams();
  const pick = (key, allowed, fallback) => (allowed.includes(p.get(key)) ? p.get(key) : fallback);
  return {
    q: p.get("q") || "",
    geometry: pick("geometry", ["polygon", "line", "point", "other"], ""),
    status: pick("status", ["measured", "not_applicable", "attention"], ""),
    warnings: pick("warnings", ["with", "without"], ""),
    sort: pick("sort", ["index", "name", "status", "area", "length", "warnings"], "index"),
    order: pick("order", ["asc", "desc"], "asc"),
    page: Math.max(1, parseInt(p.get("page") || "1", 10) || 1),
    au: p.get("au") === "ha" ? "ha" : "m2",
    lu: p.get("lu") === "km" ? "km" : "m",
  };
}

function select(id, label, value, options, onchange) {
  return h(
    "div",
    { class: "field" },
    h("label", { for: id }, label),
    h(
      "select",
      { id, class: "select", onchange },
      options.map(([v, text]) => h("option", { value: v, selected: v === value ? true : null }, text)),
    ),
  );
}

export function mountTable(region, { fileId, info, config, onSelect, onFiltersChanged, onUnitsChanged, onMissing }) {
  const state = readState();
  const pageSize = config.default_page_size;
  let rows = [];
  let total = 0;
  let selected = null;
  let mounted = true;

  const searchInput = h("input", {
    id: "f-q",
    class: "input",
    type: "search",
    value: state.q,
    placeholder: "Name, ID or attribute",
    autocomplete: "off",
    oninput: (e) => {
      state.q = e.target.value;
      debouncedFilter();
    },
  });
  const sortDir = h("button", {
    id: "sort-dir",
    type: "button",
    class: "btn btn-sm",
    style: "min-width:44px;min-height:40px",
    onclick: () => {
      state.order = state.order === "asc" ? "desc" : "asc";
      sortChanged();
    },
  });
  const matchText = h("span", { id: "match-text", style: "font-weight:500" });
  const resetInline = h("span", {}, " · ", h("button", { type: "button", class: "link-btn", onclick: () => reset() }, "Reset filters"));
  const unitButtons = {};
  const unitGroup = (label, key, pairs) =>
    h(
      "div",
      { class: "seg", role: "group", "aria-label": label },
      pairs.map(([value, text]) => {
        const btn = h("button", { type: "button", onclick: () => setUnit(key, value) }, text);
        unitButtons[`${key}:${value}`] = btn;
        return btn;
      }),
    );
  const selHidden = h("div", { id: "sel-hidden", role: "status" });
  const tableWrap = h("div", { class: "table-scroll" });
  const empty = h("div");
  const pageText = h("span", { id: "page-text" });
  const prev = h("button", { id: "prev-page", type: "button", class: "btn btn-sm", onclick: () => goPage(state.page - 1) }, "Previous");
  const next = h("button", { id: "next-page", type: "button", class: "btn btn-sm", onclick: () => goPage(state.page + 1) }, "Next");
  const headArea = h("th", { scope: "col", class: "num" });
  const headLength = h("th", { scope: "col", class: "num" });
  const tbody = h("tbody");
  const table = h(
    "table",
    { id: "feature-table", class: "data", style: "min-width:640px" },
    h("caption", { class: "vh" }, `Features in ${info.filename}`),
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("th", { scope: "col" }, "Feature"),
        h("th", { scope: "col" }, "Geometry"),
        h("th", { scope: "col" }, "Status"),
        headArea,
        headLength,
        h("th", { scope: "col" }, "Warnings"),
      ),
    ),
    tbody,
  );
  tableWrap.append(table, empty);

  region.append(
    h(
      "div",
      { role: "search", "aria-label": "Filter features", class: "toolbar" },
      h("div", { class: "field grow" }, h("label", { for: "f-q" }, "Search all features"), searchInput),
      select("f-type", "Geometry", state.geometry, [["", "All types"], ["polygon", "Polygons"], ["line", "Lines"], ["point", "Points"], ["other", "Other"]], (e) => filterChanged("geometry", e.target.value)),
      select("f-status", "Status", state.status, [["", "All statuses"], ["measured", "Measured"], ["not_applicable", "Not applicable (points)"], ["attention", "Needs attention"]], (e) => filterChanged("status", e.target.value)),
      select("f-warn", "Warnings", state.warnings, [["", "Any"], ["with", "With warnings"], ["without", "Without warnings"]], (e) => filterChanged("warnings", e.target.value)),
      h(
        "div",
        { class: "field" },
        h("label", { for: "f-sort" }, "Sort by"),
        h(
          "div",
          { style: "display:flex;gap:4px" },
          h(
            "select",
            { id: "f-sort", class: "select", onchange: (e) => { state.sort = e.target.value; sortChanged(); } },
            [["index", "File order"], ["name", "Name"], ["status", "Status"], ["area", "Area"], ["length", "Length"], ["warnings", "Warnings"]].map(([v, t]) =>
              h("option", { value: v, selected: v === state.sort ? true : null }, t),
            ),
          ),
          sortDir,
        ),
      ),
    ),
    h(
      "div",
      { class: "table-bar" },
      h("div", { "aria-live": "polite" }, matchText, resetInline),
      h("div", { class: "btn-row" }, unitGroup("Area units", "au", [["m2", "m²"], ["ha", "ha"]]), unitGroup("Length units", "lu", [["m", "m"], ["km", "km"]])),
    ),
    selHidden,
    tableWrap,
    h(
      "div",
      { class: "pager" },
      h("span", {}, "Not measured: the status explains why. n/a: doesn’t apply to this geometry type."),
      h("div", { class: "btn-row" }, prev, pageText, next),
    ),
  );

  const isFiltered = () => FILTER_KEYS.some((k) => state[k]);

  function params() {
    return { q: state.q.trim(), geometry: state.geometry, status: state.status, warnings: state.warnings, sort: state.sort, order: state.order };
  }

  function syncControls() {
    sortDir.textContent = state.order === "asc" ? "↑" : "↓";
    sortDir.setAttribute("aria-label", state.order === "asc" ? "Ascending order; switch to descending" : "Descending order; switch to ascending");
    for (const [key, btn] of Object.entries(unitButtons)) {
      const [unitKey, value] = key.split(":");
      btn.setAttribute("aria-pressed", String(state[unitKey] === value));
    }
    headArea.textContent = `Area (${fmt.AREA_UNITS[state.au].label})`;
    headLength.textContent = `Length (${fmt.LENGTH_UNITS[state.lu].label})`;
    resetInline.hidden = !isFiltered();
    region.querySelector("#f-type").value = state.geometry;
    region.querySelector("#f-status").value = state.status;
    region.querySelector("#f-warn").value = state.warnings;
    region.querySelector("#f-sort").value = state.sort;
    if (searchInput.value !== state.q) searchInput.value = state.q;
  }

  function writeUrl(push) {
    updateQuery(
      { ...params(), q: state.q.trim(), sort: state.sort === "index" ? "" : state.sort, order: state.order === "asc" ? "" : "desc", page: state.page > 1 ? state.page : "" },
      { push },
    );
  }

  function filterChanged(key, value) {
    state[key] = value;
    applyFilters();
  }

  function applyFilters() {
    state.page = 1;
    writeUrl(false);
    load();
    onFiltersChanged && onFiltersChanged(params());
  }
  const debouncedFilter = debounce(applyFilters, 300);

  function sortChanged() {
    state.page = 1;
    writeUrl(false);
    load();
    onFiltersChanged && onFiltersChanged(params());
  }

  function reset() {
    debouncedFilter.cancel();
    for (const k of FILTER_KEYS) state[k] = "";
    applyFilters();
  }

  function goPage(page) {
    state.page = page;
    writeUrl(true);
    load();
  }

  function setUnit(key, value) {
    state[key] = value;
    updateQuery({ [key]: value === "m2" || value === "m" ? "" : value });
    renderRows();
    onUnitsChanged && onUnitsChanged({ au: state.au, lu: state.lu });
  }

  function cell(value, missing) {
    return value === null ? h("td", { class: "num na" }, missing) : h("td", { class: "num" }, value);
  }

  function renderRows() {
    syncControls();
    clear(tbody);
    for (const f of rows) {
      const isSel = selected === f.index;
      const warnings = f.warnings.length;
      tbody.append(
        h(
          "tr",
          { "aria-selected": String(isSel), dataset: { index: f.index } },
          h(
            "td",
            {},
            h("button", { type: "button", class: "link-btn row-name", "aria-label": `Open details for ${f.display}`, dataset: { index: f.index }, onclick: () => onSelect(f) }, f.display),
            h("div", { class: "row-sub" }, `#${f.index} · ${f.source_id ?? "no source ID"}`),
          ),
          h("td", {}, f.geometry_type || "None"),
          h("td", {}, h("span", { class: `badge tone-${fmt.statusTone(f.status)}` }, fmt.statusLabel(f.status))),
          cell(fmt.area(f.area_m2, state.au), fmt.missingMeasurement(f, "area")),
          cell(fmt.length(f.length_m, state.lu), fmt.missingMeasurement(f, "length")),
          h("td", { class: warnings ? "" : "na", style: warnings ? "color:var(--warn-fg)" : "" }, warnings ? `${warnings} ${warnings === 1 ? "warning" : "warnings"}` : "None"),
        ),
      );
    }
    clear(empty);
    empty.removeAttribute("id");
    if (rows.length === 0 && total === 0) {
      empty.id = "table-empty";
      empty.className = "empty";
      empty.append(
        isFiltered() ? "No features match these filters. " : "This file contains no features.",
        isFiltered() ? h("button", { type: "button", class: "link-btn", onclick: () => reset() }, "Reset filters") : null,
      );
    }
    const pages = Math.max(1, Math.ceil(total / pageSize));
    pageText.textContent = `Page ${Math.min(state.page, pages)} of ${pages}`;
    prev.disabled = state.page <= 1;
    next.disabled = state.page >= pages;
    matchText.textContent = isFiltered() ? `${total} of ${info.feature_count} features match` : `All ${info.feature_count} features`;
  }

  function showError() {
    clear(tbody);
    clear(empty);
    empty.id = "table-error";
    empty.className = "empty";
    empty.append("Couldn’t load features. ", h("button", { type: "button", class: "btn btn-sm", onclick: () => load() }, "Try again"));
  }

  async function load() {
    const gen = nextGeneration("table");
    tableWrap.setAttribute("aria-busy", "true");
    try {
      const body = await api.getMeasurements(fileId, { ...params(), limit: pageSize, offset: (state.page - 1) * pageSize }, { signal: gen.signal });
      if (!gen.isCurrent() || !mounted) return;
      total = body.pagination.total;
      const pages = Math.max(1, Math.ceil(total / pageSize));
      if (state.page > pages) {
        state.page = pages; // a stale link past the last page
        writeUrl(false);
        return load();
      }
      rows = body.features.map((f) => ({ ...f, display: fmt.featureName(f) }));
      renderRows();
      tableWrap.removeAttribute("aria-busy");
      return rows;
    } catch (error) {
      if (isAbort(error) || !gen.isCurrent() || !mounted) return;
      if (error.code === "FILE_NOT_FOUND" && onMissing) { onMissing(); return; }
      tableWrap.removeAttribute("aria-busy");
      showError();
    }
  }

  syncControls();
  return {
    load,
    params,
    matching: () => total,
    rows: () => rows,
    units: () => ({ au: state.au, lu: state.lu }),
    pageSize,
    isFiltered,
    reset,
    rowFor: (index) => rows.find((f) => f.index === index) || null,
    setSelected(index) {
      selected = index;
      for (const tr of tbody.querySelectorAll("tr")) tr.setAttribute("aria-selected", String(Number(tr.dataset.index) === index));
    },
    async showOffset(offset) {
      const page = Math.floor(offset / pageSize) + 1;
      if (page !== state.page) {
        state.page = page;
        writeUrl(false);
        await load();
      }
    },
    setHiddenNotice(show) {
      clear(selHidden);
      selHidden.className = show ? "notice-info" : "";
      if (show) selHidden.append("The selected feature is hidden by the current filters. ", h("button", { type: "button", class: "link-btn", onclick: () => reset() }, "Reset filters"), " to show it.");
    },
    focusRow(index) {
      const btn = tbody.querySelector(`button[data-index="${Number(index)}"]`);
      if (btn) btn.focus();
      return Boolean(btn);
    },
    destroy() {
      mounted = false;
      debouncedFilter.cancel();
    },
  };
}
