// History-API router for the three workspace routes. Each view renders into #app and may
// return a cleanup function, called before the next view renders. View state that should
// survive a refresh or a shared link lives in the query string.
import { abortAll } from "./state.js";
import { clear } from "./dom.js";
import { closeDialogs } from "./dialogs.js";

let views = {};
let cleanup = null;
let renders = 0;

function match(pathname) {
  if (pathname === "/") return { name: "upload", params: {} };
  if (pathname === "/history") return { name: "history", params: {} };
  const files = pathname.match(/^\/files\/([^/]+)$/);
  if (files) return { name: "results", params: { id: decodeURIComponent(files[1]) } };
  return { name: "notfound", params: {} };
}

function render() {
  closeDialogs();
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  abortAll();
  const root = document.getElementById("app");
  clear(root);
  const route = match(location.pathname);
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === route.name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const view = views[route.name] || views.notfound;
  renders += 1;
  cleanup = view(root, route.params) || null;
  if (renders > 1) focusHeading();
}

// False during the very first page load, so views that render asynchronously keep the
// browser's default focus then, and move it to their heading after a navigation.
export function isNavigation() {
  return renders > 1;
}

// Moves focus to the view's heading so screen-reader and keyboard users land on the new page.
export function focusHeading() {
  const heading = document.querySelector("#app h1");
  if (heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: false });
  }
}

export function navigate(path, { replace = false } = {}) {
  if (replace) history.replaceState(null, "", path);
  else history.pushState(null, "", path);
  window.scrollTo(0, 0);
  render();
}

// Rewrites the query string without re-rendering. null/""/undefined remove a key.
export function updateQuery(changes, { push = false } = {}) {
  const params = new URLSearchParams(location.search);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === undefined || value === "") params.delete(key);
    else params.set(key, value);
  }
  const text = params.toString();
  const url = `${location.pathname}${text ? `?${text}` : ""}`;
  if (url === `${location.pathname}${location.search}`) return;
  if (push) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
}

export function queryParams() {
  return new URLSearchParams(location.search);
}

export function start(routeViews) {
  views = routeViews;
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-link]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(link.getAttribute("href"));
  });
  window.addEventListener("popstate", render);
  render();
}
