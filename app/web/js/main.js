import { start } from "./router.js";
import { renderUpload } from "./upload.js";
import { renderResults, renderNotFound } from "./results.js";
import { h } from "./dom.js";

function historyPlaceholder(root) {
  document.title = "File history · Survey measurement workspace";
  root.append(h("h1", { class: "page-title" }, "File history"));
}

start({
  upload: renderUpload,
  history: historyPlaceholder,
  results: renderResults,
  notfound: renderNotFound,
});
