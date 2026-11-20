import { apiURL } from './paths.js';
import { AppError, languageHeader } from '../ui/preferences-store.js';
/** Cookies are the only login credential; CSRF is a separate, non-auth nonce. */
// The player lives at / but loads this module from /library/. Derive API
// paths from the module location, never from asynchronously created media DOM.
const source = new URL(import.meta.url);
const library = new URL(source.pathname.includes('/chunks/') ? '../' : './', source).pathname;
let signingOut = false;
let sessionMissing = false;
export function loginLocation(target = location.pathname + location.search + location.hash) {
  return `${library}login?next=${encodeURIComponent(target)}`;
}
export function requireLogin() {
  sessionMissing = true;
  window.dispatchEvent(new Event('blur'));
  location.replace(signingOut ? `${library}login` : loginLocation());
}
export async function csrfToken(signal?: AbortSignal | null): Promise<string> {
  const value = document.cookie.split('; ').find((c) => c.startsWith('vn_stream_csrf='));
  if (value) return decodeURIComponent(value.slice('vn_stream_csrf='.length));
  const response = await fetch(apiURL('auth/session'), {
    signal,
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Accept-Language': languageHeader() },
  });
  if (response.status === 401) {
    if (!signal?.aborted) requireLogin();
    throw new AppError('ui.pleaseSignIn');
  }
  if (!response.ok) throw new AppError('ui.connectionFailedPleaseRetry');
  return (await response.json()).data.csrf_token;
}
export async function authFetch(url: URL | string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  headers.set('Accept-Language', languageHeader());
  if (!['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase()))
    headers.set('X-CSRF-Token', await csrfToken(init.signal));
  const response = await fetch(url, { ...init, credentials: 'same-origin', headers });
  if (response.status === 401 && !init.signal?.aborted) requireLogin();
  return response;
}
export async function logout() {
  // A concurrent player poll can see revocation before this response arrives.
  // Explicit sign-out keeps its library target until this document unloads.
  signingOut = true;
  try {
    const response = await authFetch(apiURL('auth/logout'), { method: 'POST' });
    if (response.status === 401) return; // authFetch has already navigated.
    if (!response.ok) throw new AppError('ui.connectionFailedPleaseRetry');
    window.dispatchEvent(new Event('blur'));
    location.replace(`${library}login`);
  } catch (error) {
    if (sessionMissing) return; // csrfToken may have confirmed expiry first.
    signingOut = false; // A network failure leaves the valid session intact.
    throw error;
  }
}
