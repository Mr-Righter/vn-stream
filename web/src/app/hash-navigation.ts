import { recordContentPosition } from './content-route.js';
import { allowDraftNavigation, hasDraftGuard } from './content-navigation.js';

const entryKey = 'renpyStreamEntry';
let acceptedHash = location.hash;
export const getHashSnapshot = () => acceptedHash;

export function subscribeHash(callback: () => void) {
  const indexOfEntry = () => {
    const value: unknown = history.state?.[entryKey];
    return typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
  };
  const markEntry = (index: number, hash = location.hash) =>
    history.replaceState(
      { ...history.state, [entryKey]: index },
      '',
      hash || location.pathname + location.search,
    );
  let acceptedIndex = indexOfEntry() ?? 0;
  acceptedHash = location.hash;
  markEntry(acceptedIndex);
  let handledHash = acceptedHash;
  let handledIndex = acceptedIndex;
  let disposed = false;
  let approvedIndex: number | null = null;
  let pending: { index: number; hash: string; asking: boolean } | null = null;
  const accept = (index: number) => {
    acceptedIndex = index;
    acceptedHash = location.hash;
    callback();
  };
  const ask = async () => {
    const target = pending;
    if (!target || target.asking) return;
    target.asking = true;
    const allowed = await allowDraftNavigation();
    if (disposed || pending !== target) return;
    pending = null;
    if (!allowed) return;
    if (target.index === acceptedIndex) {
      markEntry(acceptedIndex, target.hash);
      accept(acceptedIndex);
    } else {
      approvedIndex = target.index;
      history.go(target.index - acceptedIndex);
    }
  };
  const changed = () => {
    const next = location.hash;
    const index = indexOfEntry() ?? acceptedIndex + 1;
    if (indexOfEntry() === null) markEntry(index);
    if (next === handledHash && index === handledIndex) return;
    recordContentPosition(acceptedHash);
    handledHash = next;
    handledIndex = index;
    if (pending) {
      if (index !== acceptedIndex) history.go(acceptedIndex - index);
      else void ask();
      return;
    }
    if (next === acceptedHash && index === acceptedIndex) return;
    if (approvedIndex === index || !hasDraftGuard()) {
      approvedIndex = null;
      accept(index);
      return;
    }
    pending = { index, hash: next, asking: false };
    if (index === acceptedIndex) {
      markEntry(acceptedIndex, acceptedHash);
      void ask();
    } else {
      // Restore the accepted entry before asking; never overwrite a traversed entry.
      history.go(acceptedIndex - index);
    }
  };
  window.addEventListener('popstate', changed);
  window.addEventListener('hashchange', changed);
  return () => {
    disposed = true;
    window.removeEventListener('popstate', changed);
    window.removeEventListener('hashchange', changed);
  };
}
