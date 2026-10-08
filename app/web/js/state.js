// Request generations: starting a new load for a key aborts the previous one, and a late
// response from an older load is recognised by isCurrent() and dropped.
const current = new Map();

export function nextGeneration(key) {
  const previous = current.get(key);
  if (previous) previous.controller.abort();
  const token = { controller: new AbortController() };
  current.set(key, token);
  return { signal: token.controller.signal, isCurrent: () => current.get(key) === token };
}

export function abortAll() {
  for (const token of current.values()) token.controller.abort();
  current.clear();
}

export function isAbort(error) {
  return error && error.name === "AbortError";
}
