// Upload view: choose or drop a file, optionally supply a source CRS, upload with real byte
// progress, then open the results. Uploads are never retried automatically.
import { api } from "./api.js";
import { h, clear, icon, toast } from "./dom.js";
import { bytes, errorGuide, limit as limitText } from "./format.js";
import { navigate } from "./router.js";

// Survives route changes, so a selection is kept when the user checks history and comes back,
// and a missing-CRS result can offer the same file again.
export const session = {
  file: null, // File
  crs: "",
  phase: "idle", // idle | uploading | processing | outcome
  progress: 0,
  outcome: null, // {kind: "api_error", error} | {kind: "uncertain", startedAt}
  note: "",
  lastFile: null, // the last File uploaded successfully in this tab
};

let inFlight = false; // the double-submit guard; the disabled button is only the visual cue
let config = null;

const CRS_PATTERN = /^(epsg\s*:\s*)?\d{3,6}$/i;
const WKT_PATTERN = /^[A-Z][A-Z_]*\s*\[/i;

export function crsLooksValid(text) {
  const value = text.trim();
  return !value || CRS_PATTERN.test(value) || WKT_PATTERN.test(value);
}

function detect(file) {
  const name = file.name.toLowerCase();
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot) : "";
  if (file.size === 0) return { error: "This file is empty." };
  if (config && file.size > config.max_upload_bytes) {
    return { error: `This file is ${bytes(file.size)}. The limit is ${limitText(config.max_upload_bytes)}.` };
  }
  if (ext === ".kmz") return { error: "KMZ isn’t supported yet. Unzip the .kmz and upload the .kml file inside it." };
  if (ext === ".kml") return { format: "KML document" };
  if (ext === ".zip") return { format: "Zipped Shapefile" };
  return { error: "This file type isn’t supported. Upload a .zip containing one Shapefile, or a .kml file." };
}

export function prepareReupload(filename) {
  const sameFile = session.lastFile && session.lastFile.name === filename;
  session.file = sameFile ? session.lastFile : null;
  session.crs = "EPSG:";
  session.phase = "idle";
  session.outcome = null;
  session.note = sameFile
    ? "Enter the coordinate system the file uses, then upload it again. This creates a new record; the earlier one stays in history."
    : `Choose ${filename} again, enter the coordinate system it uses, then upload. This creates a new record; the earlier one stays in history.`;
}

export function renderUpload(root) {
  document.title = "Upload · Survey measurement workspace";
  let mounted = true;
  const parts = {};

  const fileInput = h("input", {
    id: "file-input",
    class: "vh",
    type: "file",
    accept: ".zip,.kml,.kmz",
    "aria-describedby": "limit-note",
    onchange: (event) => {
      const file = event.target.files && event.target.files[0];
      if (file) choose(file);
      event.target.value = "";
    },
  });
  const drop = h(
    "label",
    { class: "drop", for: "file-input" },
    fileInput,
    icon(["M12 16V4", "M7 9l5-5 5 5", "M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"], 28),
    h("span", { style: "font-weight:600" }, "Drop a .zip or .kml file here, or choose a file"),
    (parts.limit = h("span", { id: "limit-note", class: "small" }, "Checking the upload limit…")),
  );
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("dragging");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("dragging");
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file && !busy()) choose(file);
  });

  const crsInput = h("input", {
    id: "crs-input",
    class: "input input-lg mono",
    type: "text",
    value: session.crs,
    placeholder: "e.g. EPSG:32643",
    autocomplete: "off",
    spellcheck: "false",
    "aria-describedby": "crs-help crs-hint",
    style: "max-width:420px",
    oninput: (event) => {
      session.crs = event.target.value;
      refresh();
    },
  });
  const submit = h(
    "button",
    { id: "submit-upload", type: "button", class: "btn btn-primary", onclick: () => startUpload() },
    "Upload and measure",
  );

  parts.config = h("div");
  parts.note = h("div");
  parts.fileBox = h("div");
  parts.crsHint = h("p", { id: "crs-hint", class: "small", style: "margin:0;color:var(--danger)" });
  parts.progress = h("div", { id: "upload-progress", role: "status" });
  parts.outcome = h("div", { id: "upload-outcome", role: "alert" });
  parts.submit = submit;

  root.append(
    h(
      "section",
      { class: "upload-layout", "aria-labelledby": "upload-title" },
      h(
        "div",
        { class: "upload-main" },
        h(
          "div",
          {},
          h("h1", { id: "upload-title", class: "page-title" }, "Measure a survey file"),
          h(
            "p",
            { class: "lede" },
            "Upload a zipped Shapefile or a KML file. Every feature is read, converted from its coordinate system and measured on the ground. Anything that can’t be measured comes back with the reason.",
          ),
        ),
        parts.config,
        h(
          "div",
          { class: "panel upload-card" },
          parts.note,
          drop,
          parts.fileBox,
          h(
            "div",
            { class: "field" },
            h(
              "label",
              { for: "crs-input", style: "font-weight:600;font-size:15px;color:var(--ink)" },
              "Source coordinate system ",
              h("span", { style: "font-weight:400;color:var(--muted)" }, "(optional)"),
            ),
            crsInput,
            h(
              "p",
              { id: "crs-help", class: "small", style: "margin:0;max-width:62ch" },
              "Use this only when the file doesn’t declare one, such as a Shapefile without a .prj. It must agree with any coordinate system the file already declares, otherwise the upload is rejected. Nothing is ever guessed.",
            ),
            parts.crsHint,
          ),
          h(
            "div",
            { class: "btn-row" },
            submit,
            h("span", { class: "small" }, "Each upload creates a new record. Earlier results are never overwritten."),
          ),
          parts.progress,
          parts.outcome,
        ),
      ),
      h(
        "aside",
        { class: "upload-aside panel", "aria-labelledby": "accept-title" },
        h("h2", { id: "accept-title", class: "section-title" }, "What you can upload"),
        h(
          "ul",
          { style: "margin:0;padding-left:18px;display:flex;flex-direction:column;gap:8px;color:var(--ink-2)" },
          h("li", {}, h("strong", {}, ".zip"), " with exactly one Shapefile: ", h("span", { class: "mono small" }, ".shp .shx .dbf"), ". Folders inside the ZIP are fine."),
          h("li", {}, "A ", h("strong", {}, ".prj"), " in the ZIP tells us the Shapefile’s coordinate system. Without one, nothing is measured until you supply it."),
          h("li", {}, "A ", h("strong", {}, ".kml"), " file. KML always uses WGS 84 latitude and longitude."),
          h("li", {}, h("strong", {}, "KMZ isn’t supported yet."), " Unzip it and upload the .kml inside."),
        ),
        h("h2", { class: "section-title", style: "margin-top:18px" }, "What you get"),
        h(
          "p",
          { style: "margin:0;color:var(--ink-2)" },
          "Area in m² for polygons and length in metres for lines, measured on a projection centred on each feature and checked against an independent calculation on the WGS 84 ellipsoid.",
        ),
      ),
    ),
  );

  function busy() {
    return session.phase === "uploading" || session.phase === "processing";
  }

  function choose(file) {
    session.file = file;
    session.outcome = null;
    session.phase = "idle";
    refresh();
  }

  async function loadConfig() {
    clear(parts.config);
    try {
      config = config || (await api.getConfig());
      if (!mounted) return;
      parts.limit.textContent = `Maximum size ${limitText(config.max_upload_bytes)} (the server’s configured limit)`;
    } catch {
      if (!mounted) return;
      parts.limit.textContent = "The upload limit couldn’t be checked.";
      parts.config.append(
        h(
          "div",
          { id: "config-error", class: "notice-warn", role: "alert" },
          h("div", { class: "title" }, "The server isn’t responding"),
          h("p", { style: "margin:0" }, "Nothing has been sent. Your file and settings are kept, so you can try again."),
          h("div", {}, h("button", { type: "button", class: "btn btn-primary btn-sm", onclick: () => loadConfig() }, "Try again")),
        ),
      );
    }
    refresh();
  }

  function refresh() {
    if (!mounted) return;
    const file = session.file;
    const detected = file ? detect(file) : null;

    clear(parts.note);
    if (session.note) parts.note.append(h("p", { class: "notice-info", style: "margin:0" }, session.note));

    clear(parts.fileBox);
    if (file) {
      const box = h(
        "div",
        { class: `file-box${detected.error ? " has-error" : ""}` },
        h(
          "div",
          { style: "min-width:0" },
          h("div", { id: "file-name", class: "file-name" }, file.name),
          h("div", { id: "file-meta", class: "small" }, `${bytes(file.size)} · ${detected.format || "Not supported"}`),
        ),
        h(
          "div",
          { class: "btn-row" },
          h("label", { for: "file-input", class: "btn btn-sm" }, "Replace"),
          h(
            "button",
            {
              type: "button",
              class: "btn btn-sm",
              disabled: busy(),
              onclick: () => {
                session.file = null;
                session.outcome = null;
                session.note = "";
                refresh();
              },
            },
            "Remove",
          ),
        ),
      );
      if (detected.error) box.append(h("p", { id: "file-error", class: "file-error", role: "alert" }, detected.error));
      parts.fileBox.append(box);
    }

    const crsOk = crsLooksValid(session.crs);
    parts.crsHint.textContent = crsOk ? "" : "Enter an EPSG code such as EPSG:32643, or WKT text.";
    if (crsInput.value !== session.crs) crsInput.value = session.crs;
    crsInput.disabled = busy();
    fileInput.disabled = busy();

    const blocked = !file || Boolean(detected.error) || busy() || !crsOk || !config;
    submit.disabled = blocked;
    submit.textContent = session.phase === "uploading" ? "Uploading…" : session.phase === "processing" ? "Processing…" : "Upload and measure";

    clear(parts.progress);
    if (session.phase === "uploading") {
      const pct = Math.round(session.progress * 100);
      parts.progress.append(
        h("div", { style: "display:flex;justify-content:space-between;font-size:14px" }, h("span", { style: "font-weight:500" }, "Uploading"), h("span", { class: "mono" }, `${pct}%`)),
        h("div", { class: "progress", role: "progressbar", "aria-label": "Upload progress", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, h("div", { style: `width:${pct}%` })),
      );
    } else if (session.phase === "processing") {
      parts.progress.append(
        h("div", { style: "font-size:14px" }, h("span", { style: "font-weight:500" }, "Processing"), " · reading features and measuring them. There’s no percentage for this step; large files take a few seconds."),
        h("div", { class: "progress", "aria-hidden": "true" }, h("div", { class: "sweep" })),
      );
    }

    clear(parts.outcome);
    if (session.phase === "outcome" && session.outcome) parts.outcome.append(outcomeNotice(session.outcome));
  }

  function outcomeNotice(outcome) {
    const actions = [];
    let title;
    let body;
    let detail = null;
    if (outcome.kind === "uncertain") {
      title = "The connection dropped or the server gave no clear answer";
      body = "The outcome is uncertain: the file may or may not have been stored. Check file history before uploading again, so you don’t create a duplicate.";
      actions.push(
        h("button", {
          type: "button",
          class: "btn btn-primary btn-sm",
          onclick: () => {
            const params = new URLSearchParams({ since: outcome.startedAt });
            if (session.file) params.set("name", session.file.name);
            navigate(`/history?${params}`);
          },
        }, "Check file history"),
        h("button", { type: "button", class: "btn btn-sm", onclick: () => startUpload() }, "Upload again anyway"),
      );
    } else {
      const error = outcome.error;
      const guide = errorGuide(error.code);
      title = guide.title;
      body = guide.advice;
      detail = `${error.code}: ${error.message}`;
      if (error.code === "CRS_CONFLICT") {
        actions.push(h("button", {
          type: "button",
          class: "btn btn-primary btn-sm",
          onclick: () => {
            session.crs = "";
            session.outcome = null;
            session.phase = "idle";
            refresh();
            crsInput.focus();
          },
        }, "Clear the coordinate system"));
      }
      if (error.fileId) {
        actions.push(h("button", { type: "button", class: "btn btn-sm", onclick: () => navigate(`/files/${error.fileId}`) }, "Open the failed record"));
      }
    }
    return h(
      "div",
      { class: "notice-warn" },
      h("div", { class: "title" }, title),
      h("p", { style: "margin:0" }, body),
      detail && h("p", { class: "code-line", style: "margin:0" }, detail),
      actions.length ? h("div", { class: "btn-row" }, actions) : null,
    );
  }

  async function startUpload() {
    const file = session.file;
    if (inFlight || !file || detect(file).error || !crsLooksValid(session.crs) || !config) return;
    inFlight = true;
    session.phase = "uploading";
    session.progress = 0;
    session.outcome = null;
    refresh();
    const crs = session.crs.trim();
    try {
      const outcome = await api.upload(file, crs && crs.toUpperCase() !== "EPSG:" ? crs : "", {
        onProgress: (fraction) => {
          if (session.phase !== "uploading" && session.phase !== "processing") return;
          session.progress = fraction;
          session.phase = fraction >= 1 ? "processing" : "uploading";
          refresh();
        },
      });
      if (outcome.kind === "ok") {
        session.lastFile = file;
        session.file = null;
        session.crs = "";
        session.note = "";
        session.phase = "idle";
        session.outcome = null;
        const stillHere = mounted; // navigate() below unmounts this view
        if (stillHere) navigate(`/files/${outcome.info.id}`);
        toast(
          stillHere
            ? "Processing completed. Results are saved in file history."
            : `Processing of ${outcome.info.filename} completed. It’s in file history.`,
        );
      } else {
        session.phase = "outcome";
        session.outcome = outcome;
        refresh();
      }
    } finally {
      inFlight = false;
    }
  }

  loadConfig();
  refresh();
  if (session.note) setTimeout(() => crsInput.focus(), 0);
  return () => {
    mounted = false;
  };
}
