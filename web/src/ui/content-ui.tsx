import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button, Dropdown, Typography } from 'antd';
import {
  ArrowLeft,
  ChevronRight,
  Ellipsis,
  FileText,
  Folder,
  Home,
  Image as ImageIcon,
  Music,
  Play,
  Video,
} from 'lucide-react';
import { authFetch } from '../api/auth.js';
import { languageHeader, t } from './preferences-store.js';
import { contentURL } from '../api/content-client.js';
import { backContent, navigateContent } from '../app/content-route.js';

export function ContentHeader({
  title,
  parent,
  home,
}: {
  title: string;
  parent: string;
  home?: string;
}) {
  return (
    <header className="vn-content-heading">
      <Button
        id="back"
        type="text"
        icon={<ArrowLeft size={20} />}
        aria-label={t('content.back')}
        onClick={() => backContent(parent)}
      />
      {home && (
        <Button
          type="text"
          icon={<Home size={20} />}
          aria-label={t('assets.title')}
          onClick={() => navigateContent(home)}
        />
      )}
      <Typography.Title level={2} title={title}>
        {title}
      </Typography.Title>
    </header>
  );
}
export function ContentRow({
  icon,
  title,
  subtitle,
  trailing,
  onClick,
  testId,
}: {
  icon: ReactNode;
  title: string;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button type="button" className="vn-content-row" onClick={onClick} data-testid={testId}>
      <span className="vn-row-art">{icon}</span>
      <span className="vn-row-copy">
        <span className="vn-row-title">{title}</span>
        {subtitle && <span className="vn-row-subtitle">{subtitle}</span>}
      </span>
      {trailing && <span className="vn-row-trailing">{trailing}</span>}
      <ChevronRight className="vn-row-chevron" size={18} aria-hidden="true" />
    </button>
  );
}
export function FileIcon({ kind, size = 24 }: { kind: string; size?: number }) {
  const Icon =
    kind === 'image'
      ? ImageIcon
      : kind === 'video'
        ? Video
        : kind === 'audio'
          ? Music
          : kind === 'directory'
            ? Folder
            : FileText;
  return <Icon size={size} aria-hidden="true" />;
}
export function ContentPath({
  folder,
  root,
  onFolder,
}: {
  folder: string;
  root: string;
  onFolder: (folder: string) => void;
}) {
  const pieces = folder.split('/').filter(Boolean);
  const crumbs = [
    { name: root, path: '' },
    ...pieces.map((name, i) => ({ name, path: pieces.slice(0, i + 1).join('/') })),
  ];
  const shown = crumbs.length > 3 ? [crumbs[0], null, ...crumbs.slice(-2)] : crumbs;
  return (
    <nav
      className="vn-content-path"
      aria-label={t('content.location')}
      title={[root, ...pieces].join(' / ')}
    >
      <Folder size={18} aria-hidden="true" />
      {shown.map((item, i) => (
        <span className="vn-crumb" key={item?.path ?? 'more'}>
          {i > 0 && (
            <span className="vn-crumb-separator" aria-hidden="true">
              /
            </span>
          )}
          {item ? (
            <button type="button" onClick={() => onFolder(item.path)} title={item.name}>
              {item.name}
            </button>
          ) : (
            <Dropdown
              menu={{
                items: crumbs.slice(1, -2).map((c) => ({ key: c.path, label: c.name })),
                onClick: ({ key }) => onFolder(key),
              }}
              trigger={['click']}
            >
              <Button
                type="text"
                icon={<Ellipsis size={16} />}
                aria-label={t('content.ancestors')}
              />
            </Dropdown>
          )}
        </span>
      ))}
    </nav>
  );
}

let active = 0;
const waiting: (() => void)[] = [];
async function thumbnailSlot(signal: AbortSignal) {
  if (active >= 3) await new Promise<void>((resolve) => waiting.push(resolve));
  else active++;
  const release = () => {
    const next = waiting.shift();
    if (next) next();
    else active--;
  };
  if (signal.aborted) {
    release();
    throw new DOMException('Aborted', 'AbortError');
  }
  return release;
}
export function AssetThumbnail({
  path,
  kind,
  generation = '',
  onReady,
}: {
  path: string;
  kind: string;
  generation?: string;
  onReady?: (url: string) => void;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false),
    [url, setURL] = useState(''),
    [failed, setFailed] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      rootMargin: '160px',
    });
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setURL('');
    setFailed(false);
    if (!visible) return;
    const abort = new AbortController();
    let objectURL = '';
    let timer: ReturnType<typeof setTimeout> | undefined;
    void (async () => {
      const release = await thumbnailSlot(abort.signal);
      try {
        while (!abort.signal.aborted) {
          const response = await authFetch(
            contentURL(path) + (generation ? '?v=' + encodeURIComponent(generation) : ''),
            { signal: abort.signal, headers: { 'Accept-Language': languageHeader() } },
          );
          if (response.status === 202) {
            await new Promise<void>((resolve) => {
              timer = setTimeout(resolve, 1000);
              abort.signal.addEventListener('abort', () => resolve(), { once: true });
            });
            continue;
          }
          if (!response.ok) throw new Error('Thumbnail unavailable');
          objectURL = URL.createObjectURL(await response.blob());
          if (!abort.signal.aborted) setURL(objectURL);
          break;
        }
      } finally {
        release();
      }
    })().catch(() => {
      if (!abort.signal.aborted) setFailed(true);
    });
    return () => {
      abort.abort();
      clearTimeout(timer);
      if (objectURL) URL.revokeObjectURL(objectURL);
    };
  }, [visible, path, generation]);
  useEffect(() => {
    if (url && !failed) onReady?.(url);
  }, [url, failed, onReady]);
  return (
    <span
      ref={root}
      className="vn-asset-thumbnail"
      data-thumbnail-state={failed ? 'failed' : url ? 'ready' : 'loading'}
    >
      <FileIcon kind={kind} />
      {url && !failed && <img src={url} alt="" onError={() => setFailed(true)} />}
      {kind === 'video' && (
        <span className="vn-video-mark">
          <Play size={11} fill="currentColor" />
        </span>
      )}
    </span>
  );
}
