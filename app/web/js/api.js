// Thin client for the AEREO API. Errors carrying the API's envelope become ApiError;
// anything else (no connection, a proxy page) becomes NetworkError.
export class ApiError extends Error {
  constructor(status, code, message, fileId) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fileId = fileId ?? null;
  }
}

export class NetworkError extends Error {
  constructor(message) {
    super(message);
    this.name = "NetworkError";
  }
}

async function request(path, { signal, method = "GET" } = {}) {
  let response;
  try {
    response = await fetch(path, { signal, method, headers: { Accept: "application/json" } });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new NetworkError(String(error));
  }
  if (response.status === 204) return null;
  let body = null;
  try {
    body = await response.json();
  } catch {
    // not JSON: a proxy or gateway page
  }
  if (!response.ok) {
    const error = body && body.error;
    if (error && error.code) throw new ApiError(response.status, error.code, error.message, error.file_id);
    throw new NetworkError(`HTTP ${response.status}`);
  }
  return body;
}

export function queryString(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== null && value !== undefined && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const enc = encodeURIComponent;

export const api = {
  getConfig: (options) => request("/api/config/", options),
  getFile: (id, options) => request(`/api/files/${enc(id)}/`, options),
  getMeasurements: (id, params, options) =>
    request(`/api/files/${enc(id)}/measurements/${queryString(params)}`, options),
  getPosition: (id, index, params, options) =>
    request(`/api/files/${enc(id)}/features/${index}/position/${queryString(params)}`, options),
  listFiles: (params, options) => request(`/api/files/${queryString(params)}`, options),
  deleteFile: (id) => request(`/api/files/${enc(id)}/`, { method: "DELETE" }),
  exportUrl: (id, params) => `/api/files/${enc(id)}/export/${queryString(params)}`,

  // Resolves, never rejects. Only a response carrying the API's error envelope is a definite
  // failure. A network error, a timeout, or a 5xx without the envelope (a proxy's 502/503/504)
  // may have followed a stored upload, so the outcome is reported as uncertain.
  upload(file, sourceCrs, { onProgress } = {}) {
    const startedAt = new Date().toISOString();
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/files/");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
      };
      xhr.upload.onload = () => onProgress && onProgress(1);
      const uncertain = () => resolve({ kind: "uncertain", startedAt });
      xhr.onerror = uncertain;
      xhr.ontimeout = uncertain;
      xhr.onabort = uncertain;
      xhr.onload = () => {
        let body = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          // not ours
        }
        if (xhr.status === 201 && body && body.id) {
          resolve({ kind: "ok", info: body });
        } else if (body && body.error && body.error.code) {
          const e = body.error;
          resolve({ kind: "api_error", error: new ApiError(xhr.status, e.code, e.message, e.file_id) });
        } else {
          uncertain();
        }
      };
      const form = new FormData();
      form.append("file", file, file.name);
      if (sourceCrs) form.append("source_crs", sourceCrs);
      xhr.send(form);
    });
  },
};
