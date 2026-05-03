const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export function readCookie(name) {
  const prefix = `${name}=`;
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) || '';
}

export function csrfToken() {
  return readCookie('openscribe_csrf');
}

export function csrfHeaders(existingHeaders = {}) {
  const headers = new Headers(existingHeaders || {});
  const token = csrfToken();
  if (token) {
    headers.set('X-CSRF-Token', token);
  }
  return headers;
}

function isUnsafeApiRequest(input, init = {}) {
  const method = String(init.method || (input instanceof Request ? input.method : 'GET') || 'GET').toUpperCase();
  if (!UNSAFE_METHODS.has(method)) {
    return false;
  }

  const rawUrl = input instanceof Request ? input.url : String(input || '');
  const url = new URL(rawUrl, window.location.href);
  return url.origin === window.location.origin && url.pathname.startsWith('/api/v1/');
}

export function csrfFetch(input, init = {}) {
  if (!isUnsafeApiRequest(input, init)) {
    return fetch(input, init);
  }

  return fetch(input, {
    ...init,
    headers: csrfHeaders(init.headers || (input instanceof Request ? input.headers : undefined)),
  });
}
