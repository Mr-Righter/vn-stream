import { useEffect, useState } from 'react';
import { contentAPI } from '../../api/content-client.js';
import type { AssetEntry, AssetPreparation } from '../../api/types.js';
import { messageOf, type MessageInput } from '../../ui/preferences-store.js';

type Representation = 'original' | 'preview';
type Preparation = Partial<Record<Representation, AssetPreparation>>;

export function usePreparedAsset(projectId: string, entry: AssetEntry | null) {
  const [value, setValue] = useState<Preparation>({});
  const [error, setError] = useState<MessageInput | null>(null);
  const [attempt, setAttempt] = useState(0);
  const assetId = entry?.id;
  const required = entry?.requires_preparation ?? false;
  const separatePreview = entry?.mime === 'image/x-tlg';

  useEffect(() => {
    if (!assetId || !required) return;
    const controller = new AbortController();
    const timers = new Map<Representation, ReturnType<typeof setTimeout>>();
    const prefix = `projects/${projectId}/assets/${assetId}/prepare`;
    const { signal } = controller;

    async function update(representation: Representation, start: boolean) {
      try {
        const result = await contentAPI<AssetPreparation>(
          start ? prefix : `${prefix}?representation=${representation}`,
          start ? { representation } : undefined,
          undefined,
          signal,
        );
        if (signal.aborted) return;
        setValue((previous) => ({ ...previous, [representation]: result }));
        if (['running', 'queued', 'missing'].includes(result.state)) {
          timers.set(representation, setTimeout(() => {
            timers.delete(representation);
            void update(representation, result.state === 'missing');
          }, 700));
        } else if (result.state === 'failed') {
          setError(result.messages?.error ?? 'assets.previewUnavailable');
        }
      } catch (failure) {
        if (!signal.aborted) setError(messageOf(failure));
      }
    }

    void update('original', true);
    if (separatePreview) void update('preview', true);
    return () => {
      controller.abort();
      timers.forEach(clearTimeout);
    };
  }, [projectId, assetId, required, separatePreview, attempt]);

  return {
    required,
    original: value.original,
    preview: separatePreview ? value.preview : value.original,
    error,
    retry() {
      setValue({});
      setError(null);
      setAttempt((previous) => previous + 1);
    },
  };
}
