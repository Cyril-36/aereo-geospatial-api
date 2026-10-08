import { start } from "./router.js";
import { renderUpload } from "./upload.js";
import { renderResults, renderNotFound } from "./results.js";
import { renderHistory } from "./history.js";

start({
  upload: renderUpload,
  history: renderHistory,
  results: renderResults,
  notfound: renderNotFound,
});
