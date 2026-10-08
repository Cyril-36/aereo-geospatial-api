// Native dialogs keep keyboard focus inside the action and restore it on close.
import { api } from "./api.js";
import { h } from "./dom.js";
import { featureName } from "./format.js";

function show(dialog, onClose = () => {}) {
  const opener = document.activeElement;
  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = [...dialog.querySelectorAll("button,input,select,textarea,a[href],summary,[tabindex]")]
      .filter((el) => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length)
      .filter((el) => el.type !== "radio" || el.checked || !dialog.querySelector(`input[type="radio"][name="${el.name}"]:checked`));
    if (!controls.length) { event.preventDefault(); dialog.focus(); return; }
    const index = controls.indexOf(document.activeElement);
    const next = index < 0 ? 0 : (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
    event.preventDefault();
    controls[next].focus();
  });
  dialog.addEventListener("close", () => {
    onClose(dialog.returnValue);
    dialog.remove();
    if (opener?.isConnected) opener.focus();
  }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
}

export function closeDialogs() {
  for (const dialog of document.querySelectorAll("dialog.modal")) dialog.close();
}

export function confirmDelete(info) {
  return new Promise((resolve) => {
    const dialog = h("dialog", { class: "modal", role: "alertdialog", "aria-labelledby": "delete-title", "aria-describedby": "delete-explanation" });
    dialog.append(
      h("h2", { id: "delete-title" }, "Delete this file?"),
      h("p", { class: "file-name" }, info.filename),
      h("p", { id: "delete-explanation" }, "This removes the stored file and its results. It can’t be undone. You can upload the original again to create a new record."),
      h("div", { class: "dialog-actions" },
        h("button", { type: "button", class: "btn", autofocus: true, onclick: () => dialog.close("keep") }, "Keep it"),
        h("button", { type: "button", class: "btn btn-danger", onclick: () => dialog.close("delete") }, "Delete file")),
    );
    show(dialog, (value) => resolve(value === "delete"));
  });
}

export function openExportDialog({ fileId, info, params = {}, matching = 0, rows = [] }) {
  const filtered = ["q", "geometry", "status", "warnings"].some((key) => Boolean(params[key]));
  const dialog = h("dialog", { class: "modal", "aria-labelledby": "export-title" });
  const all = h("input", { type: "radio", name: "export-scope", value: "all", checked: true });
  const selected = h("input", { type: "radio", name: "export-scope", value: "filtered", disabled: !filtered });
  const csv = h("input", { type: "radio", name: "export-format", value: "csv", checked: true });
  const json = h("input", { type: "radio", name: "export-format", value: "json" });
  const submit = h("button", { type: "button", class: "btn btn-primary", onclick: () => download() });
  const update = () => {
    const count = selected.checked ? matching : info.feature_count;
    submit.textContent = `Download ${count} features`;
  };
  dialog.append(
    h("h2", { id: "export-title" }, "Download results"),
    h("fieldset", {}, h("legend", {}, "Features to include"),
      h("label", { class: "choice" }, all, `All features in the file (${info.feature_count})`),
      h("label", { class: "choice" }, selected, `Only features matching the current filters (${matching})`)),
    h("fieldset", {}, h("legend", {}, "Format"),
      h("label", { class: "choice" }, csv, "CSV — measurements, statuses and warnings"),
      h("label", { class: "choice" }, json, "JSON — complete results, properties and geometry")),
    h("p", { class: "small" }, "Downloads include every matching feature across all pages, including features that could not be measured. Areas are in m² and lengths in metres; full numeric precision is preserved."),
    h("details", {}, h("summary", {}, "Preview of the current page"),
      h("p", { class: "small" }, "An excerpt, not the complete download."),
      h("pre", {}, rows.slice(0, 5).map((f) => `${featureName(f)} | ${f.status} | area_m2=${f.area_m2 ?? ""} | length_m=${f.length_m ?? ""}`).join("\n") || "No features on this page.")),
    h("div", { class: "dialog-actions" },
      h("button", { type: "button", class: "btn", onclick: () => dialog.close() }, "Cancel"), submit),
  );
  dialog.addEventListener("change", update);
  function download() {
    const query = selected.checked ? { ...params } : { sort: params.sort, order: params.order };
    query.format = json.checked ? "json" : "csv";
    const link = h("a", { href: api.exportUrl(fileId, query), download: true });
    document.body.append(link);
    link.click();
    link.remove();
    dialog.close();
  }
  update();
  show(dialog);
}
