// Thin client for the Flask JSON API in routes/portal.py. Every call is
// same-origin (Flask serves the built app; Vite proxies in dev), so the
// session cookie that carries the CSRF token rides along automatically.

export class ApiError extends Error {
  constructor(message, status, { fromServer = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    // True when the message came from our own JSON {"error"} body, as
    // opposed to a fallback for a non-JSON response (rate limiter page,
    // an expired CSRF token, a proxy error...).
    this.fromServer = fromServer;
  }
}

async function request(url, { headers, ...options } = {}) {
  let response;
  try {
    response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: { Accept: 'application/json', ...headers },
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError('Could not reach the server. Check your connection and try again.', 0);
  }

  const isJson = (response.headers.get('Content-Type') || '').includes('application/json');
  const body = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    if (body && body.error) throw new ApiError(body.error, response.status, { fromServer: true });
    const fallback = response.status === 429
      ? 'Too many requests. Please wait a moment and try again.'
      : 'Something went wrong. Please try again in a moment.';
    throw new ApiError(fallback, response.status);
  }
  return body;
}

const apiBase = (slug) => `/partner-portal/${encodeURIComponent(slug)}/api`;

export function fetchPackages(slug, scope, signal) {
  const query = scope && scope !== 'all' ? `?scope=${encodeURIComponent(scope)}` : '';
  return request(`${apiBase(slug)}/packages${query}`, { signal });
}

export function fetchProducts(slug, { gender, page }, signal) {
  const params = new URLSearchParams();
  if (gender && gender !== 'all') params.set('gender', gender);
  if (page > 1) params.set('page', String(page));
  const query = params.toString();
  return request(`${apiBase(slug)}/products${query ? `?${query}` : ''}`, { signal });
}

export function fetchPackage(slug, packageId, signal) {
  return request(`${apiBase(slug)}/packages/${packageId}`, { signal });
}

export function sendInquiry(slug, packageId, fields, csrfToken) {
  return request(`${apiBase(slug)}/packages/${packageId}/inquire`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(fields),
  });
}

export function fetchSite(slug, signal) {
  return request(`${apiBase(slug)}/site`, { signal });
}

export function sendGeneralInquiry(slug, fields, csrfToken) {
  return request(`${apiBase(slug)}/inquire`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify(fields),
  });
}

/**
 * Send a form POST with the CSRF token in `tokenRef`. A non-JSON 400 is
 * Flask-WTF rejecting the token, usually because the page sat open past
 * its lifetime: `refresh()` fetches a fresh one and the send is tried
 * once more before giving up.
 */
export async function withCsrfRetry(tokenRef, send, refresh) {
  try {
    return await send(tokenRef.current);
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 400 || err.fromServer) throw err;
    tokenRef.current = await refresh();
    return send(tokenRef.current);
  }
}
