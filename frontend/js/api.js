// Thin client for the Scrobbler /api/v1 JSON API.

const TOKEN_KEY = "scrobbler.token";

export const apiBase = (window.SCROBBLER_CONFIG?.apiBaseUrl || "http://localhost:5050").replace(/\/+$/, "");

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable (private mode): the session lasts until the tab closes.
  }
}

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// Only same-site page names are allowed as a post-login destination.
export function safeNext(value) {
  return /^[a-z]+\.html(\?[\w=&%.-]*)?$/.test(value || "") ? value : "index.html";
}

export function redirectToLogin() {
  const here = location.pathname.split("/").pop() || "index.html";
  location.href = `login.html?next=${encodeURIComponent(safeNext(here + location.search))}`;
}

// Fetches a binary resource (e.g. album art) with the same bearer-auth transport as api(),
// returning a blob: object URL, or null on any failure (including a 404 "no art" response) -
// the caller treats "missing" and "couldn't fetch" the same way: show a placeholder.
export async function fetchImage(path) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${apiBase}/api/v1${path}`, { headers });
  } catch {
    return null;
  }
  if (response.status === 401) {
    setToken(null);
    redirectToLogin();
    return null;
  }
  if (!response.ok) return null;
  return URL.createObjectURL(await response.blob());
}

export async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", `Can't reach the Scrobbler API at ${apiBase}. Check that it's running.`);
  }

  if (response.status === 401 && auth) {
    setToken(null);
    redirectToLogin();
    throw new ApiError(401, "unauthorized", "Your session has ended. Sign in again.");
  }
  if (response.status === 204) return null;

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error ?? {};
    throw new ApiError(
      response.status,
      error.code ?? "error",
      error.message ?? `The API returned an error (${response.status}).`,
      error.details,
    );
  }
  return data;
}

// Turn webargs validation details into a single readable sentence.
export function describeError(error) {
  const fields = error.details?.json ?? error.details?.query ?? error.details?.files;
  if (fields) {
    return Object.entries(fields)
      .map(([field, messages]) => `${field}: ${[].concat(messages).join(" ")}`)
      .join(" ");
  }
  return error.message;
}

// Multipart upload with progress (fetch can't report upload progress).
export function upload(path, formData, onProgress, method = "POST") {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(method, `${apiBase}/api/v1${path}`);
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });
    xhr.addEventListener("error", () =>
      reject(new ApiError(0, "network", `Can't reach the Scrobbler API at ${apiBase}. Check that it's running.`)),
    );
    xhr.addEventListener("load", () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        // Not JSON (e.g. a proxy's error page)
      }
      if (xhr.status === 401) {
        setToken(null);
        redirectToLogin();
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
        return;
      }
      const error = data?.error ?? {};
      reject(
        new ApiError(
          xhr.status,
          error.code ?? "error",
          error.message ?? (xhr.status === 413 ? "That file is too large." : `Upload failed (${xhr.status}).`),
          error.details,
        ),
      );
    });
    xhr.send(formData);
  });
}
