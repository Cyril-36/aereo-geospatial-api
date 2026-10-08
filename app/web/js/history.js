// Persistent file history. Searching, filtering and paging use the server's database.
import { api, ApiError } from "./api.js";
import { h, clear, debounce, toast } from "./dom.js";
import * as fmt from "./format.js";
import { navigate, queryParams, updateQuery, focusHeading } from "./router.js";
import { nextGeneration, isAbort } from "./state.js";
import { confirmDelete } from "./dialogs.js";

export function renderHistory(root) {
  document.title = "File history · Survey measurement workspace";
  const params = queryParams();
  const state = {
    q: params.get("q") || "", status: params.get("status") || "", format: params.get("format") || "",
    since: params.get("since") || "", offset: Math.max(0, Number(params.get("offset")) || 0),
  };
  let mounted = true;
  let pagination = null;
  const search = h("input", { id: "h-q", class: "input", type: "search", value: state.q, maxlength: 200, oninput: (e) => { state.q = e.target.value; delayed(); } });
  const select = (id, label, key, options) => h("div", { class: "field" }, h("label", { for: id }, label),
    h("select", { id, class: "select", onchange: (e) => { state[key] = e.target.value === "all" ? "" : e.target.value; changed(); } },
      options.map(([value, text]) => h("option", { value, selected: (state[key] || "all") === value }, text))));
  const empty = h("div", { id: "history-empty", class: "empty", role: "status" });
  const tbody = h("tbody");
  const table = h("table", { id: "history-table", class: "data" },
    h("caption", { class: "vh" }, "Previously uploaded files, newest first"),
    h("thead", {}, h("tr", {}, ["File", "Uploaded", "Status", "Features", "Actions"].map((name) => h("th", { scope: "col" }, name)))), tbody);
  const tableWrap = h("div", { class: "table-scroll" }, table);
  const count = h("span", { role: "status" });
  const previous = h("button", { class: "btn btn-sm", type: "button", onclick: () => page(Math.max(0, state.offset - pagination.limit)) }, "Previous");
  const next = h("button", { class: "btn btn-sm", type: "button", onclick: () => page(pagination.next_offset) }, "Next");
  const id = h("input", { id: "h-id", class: "input", required: true, placeholder: "Paste a file ID" });
  const reset = h("button", { class: "btn btn-sm", type: "button", onclick: () => {
    state.q = state.status = state.format = state.since = "";
    search.value = "";
    root.querySelector("#h-status").value = root.querySelector("#h-format").value = "all";
    sinceNotice.hidden = true;
    changed();
  } }, "Reset filters");
  const sinceNotice = h("div", { id: "history-since", class: "notice-info", hidden: !state.since },
    `Checking uploads since you started uploading ${params.get("name") || "your file"}. `,
    "A recent record may still be processing. If none appears, wait and refresh before deciding to upload again.");
  const refresh = h("button", { class: "btn btn-sm", type: "button", onclick: () => load() }, "Refresh history");
  root.append(h("section", { class: "history" },
    h("h1", { class: "page-title" }, "File history"),
    h("p", { class: "lede" }, "Reopen previous uploads, inspect failures or remove files you no longer need."),
    sinceNotice,
    h("div", { class: "toolbar" }, h("div", { class: "field grow" }, h("label", { for: "h-q" }, "Search filename or ID"), search),
      select("h-status", "Processing status", "status", [["all", "All statuses"], ...["COMPLETED", "FAILED", "PROCESSING", "EXTRACTED"].map((v) => [v, v])]),
      select("h-format", "File format", "format", [["all", "All formats"], ["SHAPEFILE", "Shapefile"], ["KML", "KML"]]), reset, refresh),
    h("form", { class: "btn-row", onsubmit: (e) => { e.preventDefault(); navigate(`/files/${encodeURIComponent(id.value.trim())}`); } },
      h("label", { for: "h-id" }, "Open by file ID"), id, h("button", { class: "btn btn-sm", type: "submit" }, "Open")),
    tableWrap, empty,
    h("div", { class: "pager" }, count, h("div", { class: "btn-row" }, previous, next)),
  ));
  function write(push = false) {
    updateQuery({ q: state.q, status: state.status, format: state.format, since: state.since, offset: state.offset || "" }, { push });
  }
  function changed() { state.offset = 0; write(); load(); }
  const delayed = debounce(changed, 300);
  function page(offset) { state.offset = offset; write(true); load(); }
  async function remove(info, button) {
    if (!(await confirmDelete(info)) || !mounted) return;
    button.disabled = true;
    try {
      await api.deleteFile(info.id);
      if (!mounted) return;
      toast(`Deleted ${info.filename}. Stored-upload cleanup may be pending after an I/O error.`);
      await load();
      focusHeading();
    } catch (error) {
      if (!mounted) return;
      if (error instanceof ApiError && error.status === 404) { toast("This file was already deleted."); load(); }
      else { button.disabled = false; toast(fmt.errorGuide(error.code).advice || error.message); }
    }
  }
  async function load() {
    const gen = nextGeneration("history");
    tableWrap.setAttribute("aria-busy", "true");
    previous.disabled = next.disabled = true;
    empty.textContent = "Loading file history…";
    try {
      const body = await api.listFiles(state, { signal: gen.signal });
      if (!gen.isCurrent() || !mounted) return;
      pagination = body.pagination;
      if (state.offset >= pagination.total && state.offset > 0) {
        state.offset = Math.max(0, Math.ceil(pagination.total / pagination.limit) - 1) * pagination.limit;
        write(); return load();
      }
      clear(tbody);
      for (const info of body.files) {
        const removeButton = h("button", { type: "button", class: "btn btn-sm", "aria-label": `Delete ${info.filename}`, disabled: info.status === "PROCESSING" }, "Delete");
        removeButton.addEventListener("click", () => remove(info, removeButton));
        tbody.append(h("tr", {},
          h("td", {}, h("button", { type: "button", class: "link-btn row-name", onclick: () => navigate(`/files/${info.id}`) }, info.filename), h("div", { class: "small" }, fmt.formatName(info.format)), h("div", { class: "row-sub" }, info.id)),
          h("td", {}, fmt.when(info.created_at)),
          h("td", {}, h("span", { class: `badge tone-${info.status === "COMPLETED" ? "ok" : info.status === "FAILED" ? "warn" : "na"}` }, info.status), info.error && h("div", { class: "code-line" }, info.error.code)),
          h("td", { class: "num" }, info.feature_count ?? "—"), h("td", {}, removeButton)));
      }
      const filtered = Boolean(state.q || state.status || state.format || state.since);
      empty.textContent = body.files.length ? "" : filtered ? "No files match these filters. Reset them or refresh history." : "No files yet. Upload a Shapefile or KML to get started.";
      tableWrap.hidden = body.files.length === 0;
      count.textContent = `${pagination.total} ${pagination.total === 1 ? "file" : "files"} · Page ${Math.floor(state.offset / pagination.limit) + 1} of ${Math.max(1, Math.ceil(pagination.total / pagination.limit))}`;
      previous.disabled = state.offset === 0;
      next.disabled = pagination.next_offset === null;
    } catch (error) {
      if (isAbort(error) || !gen.isCurrent() || !mounted) return;
      clear(tbody); tableWrap.hidden = true;
      clear(empty).append("Couldn’t load file history. ", h("button", { class: "btn btn-sm", type: "button", onclick: () => load() }, "Try again"));
    } finally {
      if (gen.isCurrent() && mounted) tableWrap.removeAttribute("aria-busy");
    }
  }
  load();
  return () => { mounted = false; delayed.cancel(); };
}
