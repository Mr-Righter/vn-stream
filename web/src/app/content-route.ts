import { useLayoutEffect } from 'react';

export type ContentRoute = {
  projectId: string;
  tab: 'info' | 'assets' | 'saves';
  view: 'home' | 'browse' | 'asset' | 'save' | 'migrate';
  uid: string;
  params: URLSearchParams;
};
const positions = new Map<string, number>();
const parents = new Map<string, string>();
export function parseContentRoute(hash: string): ContentRoute | null {
  const [path, query = ''] = hash.replace(/^#/, '').split('?');
  const parts = path.split('/');
  if (parts[0] !== 'projects' || !parts[1]) return null;
  const tab = parts[2] === 'assets' ? 'assets' : parts[2] === 'saves' ? 'saves' : 'info';
  return {
    projectId: parts[1],
    tab,
    view:
      parts[3] === 'migrate' && tab === 'saves'
        ? 'migrate'
        : parts[3] === 'browse'
          ? 'browse'
          : parts[3] === 'item'
            ? tab === 'saves'
              ? 'save'
              : 'asset'
            : 'home',
    uid: parts[4] ?? '',
    params: new URLSearchParams(query),
  };
}
export function contentHash(
  projectId: string,
  tab: string,
  view = '',
  uid = '',
  params?: URLSearchParams,
) {
  const path = ['projects', projectId, tab === 'info' ? '' : tab, view, uid]
    .filter(Boolean)
    .join('/');
  const query = params?.toString();
  return '#' + path + (query ? '?' + query : '');
}
function scrollKey(hash: string) {
  const route = parseContentRoute(hash);
  return route?.view === 'home' ? contentHash(route.projectId, 'info') : hash;
}
export function recordContentPosition(hash: string) {
  positions.set(scrollKey(hash), window.scrollY);
}
export function navigateContent(hash: string, replace = false) {
  if (hash === location.hash) return;
  recordContentPosition(location.hash);
  if (!replace) {
    parents.set(hash, location.hash);
    location.hash = hash;
  } else {
    const oldURL = location.href;
    history.replaceState(history.state, '', hash);
    dispatchEvent(new HashChangeEvent('hashchange', { oldURL, newURL: location.href }));
  }
}
export function backContent(fallback: string) {
  recordContentPosition(location.hash);
  if (parents.get(location.hash) === fallback) history.back();
  else navigateContent(fallback);
}
export function useContentScroll(hash: string) {
  const key = scrollKey(hash);
  useLayoutEffect(() => {
    const previous = history.scrollRestoration;
    history.scrollRestoration = 'manual';
    return () => {
      history.scrollRestoration = previous;
    };
  }, []);
  useLayoutEffect(() => {
    const record = () => positions.set(key, window.scrollY);
    const frame = requestAnimationFrame(() => {
      window.scrollTo(0, positions.get(key) ?? 0);
      window.addEventListener('scroll', record, { passive: true });
    });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', record);
    };
  }, [key]);
}
export function changedParams(params: URLSearchParams, changes: Record<string, string>) {
  const next = new URLSearchParams(params);
  for (const [key, value] of Object.entries(changes)) {
    if (value && !(key === 'page' && value === '1')) next.set(key, value);
    else next.delete(key);
  }
  return next;
}
