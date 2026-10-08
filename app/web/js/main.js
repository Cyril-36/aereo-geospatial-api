import { start } from "./router.js";
import { h } from "./dom.js";

function placeholder(title) {
  return (root) => {
    document.title = `${title} · Survey measurement workspace`;
    root.append(h("h1", { class: "page-title" }, title));
  };
}

start({
  upload: placeholder("Measure a survey file"),
  history: placeholder("File history"),
  results: placeholder("Results"),
  notfound: placeholder("Page not found"),
});
