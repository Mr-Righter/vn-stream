import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Image,
  Progress,
  Skeleton,
  Space,
  Tag,
  Typography,
} from 'antd';
import { ChevronLeft, ChevronRight, Download, RotateCw } from 'lucide-react';
import { t, messageOf, type MessageInput } from '../../ui/preferences-store.js';
import { bytes } from '../../ui/format.js';
import { contentAPI, contentURL, useContent } from '../../api/content-client.js';
import { contentHash, navigateContent, type ContentRoute } from '../../app/content-route.js';
import { ContentHeader, FileIcon, AssetThumbnail } from '../../ui/content-ui.js';
import { AudioPlayer } from './audio-player.js';
import { usePreparedAsset } from './use-prepared-asset.js';
import type { AssetDetail as Detail, PreviewState } from '../../api/generated/types.gen.js';

export function AssetDetail({ projectId, route }: { projectId: string; route: ContentRoute }) {
  const prefix = `projects/${projectId}/assets`,
    uid = route.uid;
  const context = new URLSearchParams(route.params);
  context.delete('page');
  const detail = useContent<Detail>(`${prefix}/${uid}?${context}`);
  const entry = detail.value?.id === uid ? detail.value : null;
  const prepared = usePreparedAsset(projectId, entry);
  const [compatible, setCompatible] = useState(false),
    [status, setStatus] = useState<{ state: string; progress_percent: number } | null>(null),
    [error, setError] = useState<MessageInput | null>(null),
    [text, setText] = useState(''),
    [poster, setPoster] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const originalURL = prepared.required
    ? prepared.original?.url
    : contentURL(`${prefix}/${uid}/content`);
  const preparedReady = !prepared.required || prepared.preview?.state === 'completed';
  const url = compatible
    ? contentURL(`${prefix}/${uid}/preview/content`)
    : prepared.required
      ? (prepared.preview?.url ?? '')
      : contentURL(`${prefix}/${uid}/content`);
  useEffect(() => {
    const node = video.current;
    return () => {
      node?.pause();
      node?.removeAttribute('src');
      node?.load();
    };
  }, [url]);
  useEffect(() => {
    if (!compatible) return;
    const controller = new AbortController();
    const { signal } = controller;
    let timer: ReturnType<typeof setTimeout>;
    const previewPath = `${prefix}/${uid}/preview`;
    async function poll(start: boolean) {
      try {
        const result = await contentAPI<PreviewState>(
          previewPath,
          start ? {} : undefined,
          undefined,
          signal,
        );
        if (signal.aborted) return;
        setStatus(result);
        if (result.state === 'running' || result.state === 'queued') {
          timer = setTimeout(() => void poll(false), 1000);
        }
      } catch (failure) {
        if (!signal.aborted) setError(messageOf(failure));
      }
    }
    void poll(true);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [compatible, uid, prefix]);
  useEffect(() => {
    if (entry?.kind !== 'script' || !/\.(rpy|py|txt|json|md|csv)$/i.test(entry.name)) return;
    const controller = new AbortController();
    const { signal } = controller;
    void contentAPI<{ text: string }>(`${prefix}/${uid}/text`, undefined, 'GET', signal)
      .then((value) => {
        if (!signal.aborted) setText(value.text);
      })
      .catch((failure) => {
        if (!signal.aborted) setError(messageOf(failure));
      });
    return () => controller.abort();
  }, [entry?.id, entry?.kind, entry?.name, prefix, uid]);
  const parent = contentHash(projectId, 'assets', 'browse', '', route.params);
  const playable = preparedReady && (!compatible || status?.state === 'completed');
  const mediaError = () => {
    if (!compatible) setCompatible(true);
    else setError('assets.previewUnavailable');
  };
  return (
    <article className="vn-content-page vn-asset-detail">
      <ContentHeader
        title={entry?.name ?? t('assets.title')}
        parent={parent}
        home={contentHash(projectId, 'assets')}
      />
      {(error || detail.error || prepared.error) && (
        <Alert type="error" title={t(error ?? detail.error ?? prepared.error!)} />
      )}
      {!entry && detail.loading && <Skeleton active />}
      {entry && (
        <>
          <div className="vn-content-toolbar">
            <Tag icon={<FileIcon kind={entry.kind} size={15} />}>{t(`assets.${entry.kind}`)}</Tag>
            <Space>
              <Button
                type="text"
                icon={<ChevronLeft size={20} />}
                aria-label={t('assets.previous')}
                disabled={!entry.previous_id}
                onClick={() =>
                  navigateContent(
                    contentHash(projectId, 'assets', 'item', entry.previous_id!, route.params),
                  )
                }
              />
              <Typography.Text className="vn-media-position">
                {entry.position} / {entry.total}
              </Typography.Text>
              <Button
                type="text"
                icon={<ChevronRight size={20} />}
                aria-label={t('assets.next')}
                disabled={!entry.next_id}
                onClick={() =>
                  navigateContent(
                    contentHash(projectId, 'assets', 'item', entry.next_id!, route.params),
                  )
                }
              />
            </Space>
          </div>
          <div className="vn-asset-stage">
            {!preparedReady && !prepared.error && entry.preview_supported !== false && (
              <div className="vn-conversion-state" data-testid="asset-preparing">
                <Progress percent={Math.floor(prepared.preview?.progress_percent ?? 0)} />
                <Typography.Text>{t('assets.preparing')}</Typography.Text>
              </div>
            )}
            {prepared.error && entry.preview_supported !== false && (
              <Empty description={t(prepared.error)}>
                <Button icon={<RotateCw size={16} />} onClick={prepared.retry}>
                  {t('ui.retryRequest')}
                </Button>
              </Empty>
            )}
            {entry.preview_supported === false && (
              <Empty description={t('assets.formatUnsupported', { format: entry.format ?? '' })} />
            )}
            {entry.kind === 'image' && preparedReady && entry.preview_supported !== false && (
              <Image key={uid} src={url} alt={entry.name} />
            )}
            {entry.kind === 'video' && preparedReady && entry.preview_supported !== false && (
              <>
                {!playable ? (
                  <div className="vn-conversion-state">
                    <Progress
                      percent={Math.floor(status?.progress_percent ?? 0)}
                      status={status?.state === 'failed' ? 'exception' : 'active'}
                    />
                    <Typography.Text>
                      {t(
                        status?.state === 'failed'
                          ? 'assets.previewUnavailable'
                          : 'assets.converting',
                      )}
                    </Typography.Text>
                  </div>
                ) : (
                  <video
                    key={url}
                    ref={video}
                    src={url}
                    poster={poster}
                    controls
                    playsInline
                    preload="metadata"
                    className="vn-media-player"
                    onError={mediaError}
                  />
                )}
                <div className="vn-poster-preload" aria-hidden="true">
                  <AssetThumbnail
                    path={`${prefix}/${uid}/thumbnail`}
                    kind="video"
                    onReady={setPoster}
                  />
                </div>
              </>
            )}
            {entry.kind === 'audio' &&
              preparedReady &&
              entry.preview_supported !== false &&
              (playable ? (
                <AudioPlayer
                  key={url}
                  src={url}
                  name={entry.name}
                  size={entry.size_bytes}
                  onError={mediaError}
                />
              ) : (
                <div className="vn-conversion-state">
                  <Progress
                    percent={Math.floor(status?.progress_percent ?? 0)}
                    status={status?.state === 'failed' ? 'exception' : 'active'}
                  />
                  <Typography.Text>{t('assets.converting')}</Typography.Text>
                </div>
              ))}
            {text && <pre className="vn-text-preview">{text}</pre>}
            {entry.kind === 'other' && <Empty description={t('assets.previewUnavailable')} />}
          </div>
          <div className="vn-asset-caption">
            <Typography.Title level={3}>{entry.name}</Typography.Title>
            <Space>
              <Tag>{entry.format ?? entry.name.split('.').pop()?.toUpperCase()}</Tag>
              <Tag>{bytes(entry.size_bytes)}</Tag>
            </Space>
          </div>
          <Descriptions
            title={t('content.details')}
            column={1}
            className="vn-file-info"
            bordered
            size="small"
            items={[
              {
                key: 'path',
                label: t('content.path'),
                children: <span className="vn-file-path">{entry.path}</span>,
              },
              ...(entry.archive !== undefined
                ? [
                    {
                      key: 'archive',
                      label: t('assets.sourceArchive'),
                      children: (
                        <span className="vn-file-path">
                          {entry.archive ?? t('assets.looseFiles')}
                        </span>
                      ),
                    },
                    {
                      key: 'internal',
                      label: t('assets.internalPath'),
                      children: <span className="vn-file-path">{entry.internal_path}</span>,
                    },
                  ]
                : []),
              { key: 'size', label: t('content.size'), children: bytes(entry.size_bytes) },
              { key: 'type', label: t('content.type'), children: t(`assets.${entry.kind}`) },
            ]}
          />
          <Space wrap>
            <Button
              type="primary"
              icon={<Download size={16} />}
              disabled={!originalURL}
              href={
                originalURL
                  ? `${originalURL}${originalURL.includes('?') ? '&' : '?'}download=true`
                  : undefined
              }
            >
              {t('assets.download')}
            </Button>
            {['video', 'audio'].includes(entry.kind) &&
              preparedReady &&
              entry.preview_supported !== false && (
                <Button disabled={compatible} onClick={() => setCompatible(true)}>
                  {t('assets.compatible')}
                </Button>
              )}
          </Space>
          {compatible && (
            <Typography.Paragraph type="secondary">{t('assets.previewNote')}</Typography.Paragraph>
          )}
        </>
      )}
    </article>
  );
}
