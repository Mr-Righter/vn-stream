import { App } from 'antd';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { contentAPI } from '../../api/content-client.js';
import type { SaveVariables } from '../../api/types.js';
import { messageOf, t, type MessageInput } from '../../ui/preferences-store.js';

export async function readSaveRevision(path: string) {
  return (await contentAPI<SaveVariables>(`${path}/variables`)).revision;
}

export function useViewLifetime() {
  const active = useRef(false);
  useLayoutEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  return useCallback(() => active.current, []);
}

export function useSaveActions(onChanged: () => void) {
  const { message, modal } = App.useApp();
  const isActive = useViewLifetime();
  const confirmation = useRef<ReturnType<typeof modal.confirm> | null>(null);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageInput | null>(null);
  useLayoutEffect(() => () => confirmation.current?.destroy(), []);

  function confirm(options: Parameters<typeof modal.confirm>[0]) {
    confirmation.current?.destroy();
    confirmation.current = modal.confirm(options);
  }

  async function run(action: () => Promise<unknown>) {
    if (!isActive() || pending.current) return false;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      if (isActive()) void message.success(t('saves.done'));
      return true;
    } catch (e) {
      if (isActive()) setError(messageOf(e));
      return false;
    } finally {
      pending.current = false;
      if (isActive()) setBusy(false);
    }
  }

  return { busy, error, setError, confirm, run, isActive };
}
