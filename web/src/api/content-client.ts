import { useCallback, useEffect, useState } from 'react';
import { uploadBytes } from './binary-upload.js';
import { messageOf, type MessageInput } from '../ui/preferences-store.js';
import { apiURL, requestResource } from './client.js';

export const contentURL = (path: string) => apiURL(path).toString();

export const contentAPI = <T>(path: string, body?: unknown, method?: string, signal?: AbortSignal) =>
  requestResource<T>(path, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    signal,
    ...(body === undefined ? {} : {
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  }).then(({ data, pagination }) => pagination && data && typeof data === 'object'
    ? { ...data, pagination, total: pagination.total }
    : data);

type ContentState<T> = {
  path: string;
  refresh: number;
  value: T | null;
  error: MessageInput | null;
  loading: boolean;
};

export function useContent<T>(path: string | null, refresh = 0, { enabled = true } = {}) {
  const [state, setState] = useState<ContentState<T> | null>(null);
  useEffect(() => {
    if (!path || !enabled) return;
    const controller = new AbortController();
    const { signal } = controller;
    setState((previous) => ({
      path, refresh, value: previous?.path === path ? previous.value : null,
      loading: true, error: null,
    }));
    void contentAPI<T>(path, undefined, 'GET', signal).then(
      (value) => {
        if (!signal.aborted) setState({ path, refresh, value, loading: false, error: null });
      },
      (error) => {
        if (!signal.aborted) setState((previous) => ({
          path, refresh, value: previous?.path === path ? previous.value : null,
          loading: false, error: messageOf(error),
        }));
      },
    );
    return () => controller.abort();
  }, [path, refresh, enabled]);
  const setValue = useCallback((next: T | null | ((value: T | null) => T | null)) => {
    setState((previous) => {
      if (previous?.path !== path) return previous;
      return { ...previous, value: typeof next === 'function'
        ? (next as (value: T | null) => T | null)(previous.value)
        : next };
    });
  }, [path]);
  const matches = state?.path === path;
  const current = matches && state.refresh === refresh;
  return {
    value: matches ? state.value : null,
    error: current ? state.error : null,
    loading: Boolean(enabled && path && (!current || state.loading)),
    setValue,
  };
}

export async function uploadSave(path: string, file: File, progress: (value: number) => void) {
  await uploadBytes(path, file, {
    method: 'POST',
    progress: (event) => {
      if (event.lengthComputable) progress(Math.floor((event.loaded / event.total) * 100));
    },
  });
}
