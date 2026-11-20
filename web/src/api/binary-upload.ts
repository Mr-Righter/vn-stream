import { csrfToken, requireLogin } from './auth.js';
import { apiError, apiURL } from './client.js';
import { AppError, languageHeader } from '../ui/preferences-store.js';

export async function uploadBytes<T>(
  path: string,
  bytes: Blob,
  { method = 'PUT', signal, progress }: {
    method?: 'PUT' | 'POST';
    signal?: AbortSignal;
    progress?: (event: ProgressEvent) => void;
  } = {},
): Promise<T> {
  const csrf = await csrfToken(signal);
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const failed = () => reject(new AppError('ui.connectionFailedPleaseRetry'));
    xhr.open(method, apiURL(path));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.setRequestHeader('Accept-Language', languageHeader());
    xhr.timeout = 30000;
    xhr.upload.onprogress = progress ?? null;
    xhr.onerror = xhr.ontimeout = failed;
    xhr.onabort = () => reject(new DOMException('Cancelled', 'AbortError'));
    xhr.onloadend = () => signal?.removeEventListener('abort', abort);
    xhr.onload = () => {
      let payload: unknown;
      try { payload = JSON.parse(xhr.responseText); } catch { payload = null; }
      if (xhr.status === 401) requireLogin();
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(apiError(payload, xhr.status));
        return;
      }
      if (!payload || typeof payload !== 'object' || !('data' in payload)) {
        reject(new AppError('ui.invalidUploadResponse'));
        return;
      }
      resolve(payload.data as T);
    };
    signal?.addEventListener('abort', abort, { once: true });
    xhr.send(bytes);
  });
}
