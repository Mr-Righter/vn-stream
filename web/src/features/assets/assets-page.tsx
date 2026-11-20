import type { AssetListEntry, AssetTask, AssetSummary } from '../../api/types.js';
import { TaskProgress } from '../../ui/task-progress.js';
import { useEffect, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Collapse,
  Empty,
  Input,
  Pagination,
  Space,
  Tag,
  Typography,
} from 'antd';
import { Image as ImageIcon, RefreshCw, CircleAlert } from 'lucide-react';
import { t, messageOf, type MessageInput } from '../../ui/preferences-store.js';
import { bytes, date } from '../../ui/format.js';
import { contentAPI, useContent } from '../../api/content-client.js';
import {
  contentHash,
  changedParams,
  navigateContent,
  type ContentRoute,
} from '../../app/content-route.js';
import {
  AssetThumbnail,
  ContentHeader,
  ContentPath,
  ContentRow,
  FileIcon,
} from '../../ui/content-ui.js';
import { AssetDetail } from './asset-detail.js';

type Entry = AssetListEntry;
type Job = AssetTask;
type Summary = AssetSummary;
const categories = ['image', 'audio', 'video', 'script', 'other'];
export function AssetsPage({
  projectId,
  hidden,
  route,
}: {
  projectId: string;
  hidden: boolean;
  route: ContentRoute;
}) {
  const [refresh, setRefresh] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<MessageInput | null>(null);
  const { modal } = App.useApp();
  const prefix = `projects/${projectId}/assets`,
    params = route.params;
  const category = params.get('category') ?? '',
    folder = params.get('folder') ?? '',
    search = params.get('search') ?? '';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const summary = useContent<Summary>(prefix, refresh, { enabled: !hidden });
  const info = summary.value,
    job = info?.job,
    running = job?.state === 'running';
  const warnings = info?.warning_details ?? [];
  const warningCount = warnings.length || job?.warnings || 0;
  const listing = useContent<{
    entries: Entry[];
    pagination: { total: number; page: number; page_size: number };
  }>(
    info?.ready && route.view === 'browse'
      ? `${prefix}/entries?${new URLSearchParams({ category, folder, search, page: String(page) })}`
      : null,
    refresh,
    { enabled: !hidden },
  );
  const browseHash = (changes: Record<string, string> = {}) =>
    contentHash(projectId, 'assets', 'browse', '', changedParams(params, changes));
  const browse = (kind: string) =>
    navigateContent(
      contentHash(
        projectId,
        'assets',
        'browse',
        '',
        new URLSearchParams(kind ? { category: kind } : {}),
      ),
    );
  useEffect(() => {
    if (!running || hidden) return;
    const timer = setTimeout(() => setRefresh((v) => v + 1), 1000);
    return () => clearTimeout(timer);
  }, [running, hidden, refresh]);
  async function extract() {
    setBusy(true);
    setError(null);
    try {
      await contentAPI(`${prefix}/extract`, { request_id: crypto.randomUUID() });
      setRefresh((v) => v + 1);
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section hidden={hidden} aria-label={t('assets.title')} className="vn-content-page">
      {route.view === 'asset' && !hidden && (
        <AssetDetail key={route.uid} projectId={projectId} route={route} />
      )}
      <div hidden={route.view === 'asset'} className="vn-content-page">
        {route.view === 'browse' && (
          <ContentHeader
            title={folder.split('/').pop() || t(category ? `assets.${category}` : 'assets.all')}
            parent={
              folder
                ? browseHash({ folder: folder.split('/').slice(0, -1).join('/'), page: '1' })
                : contentHash(projectId, 'assets')
            }
            home={contentHash(projectId, 'assets')}
          />
        )}
        {summary.loading && !info && <Card loading />}
        {(error || summary.error) && (
          <Alert type="error" showIcon title={t(error ?? summary.error!)} />
        )}
        {info && !info.ready && !running && (
          <Card className="vn-content-empty">
            <Empty
              image={<ImageIcon size={48} aria-hidden="true" />}
              description={
                <>
                  <Typography.Title level={3}>{t('assets.emptyTitle')}</Typography.Title>
                  <Typography.Paragraph type="secondary">
                    {t('assets.emptyDescription')}
                  </Typography.Paragraph>
                </>
              }
            >
              <Button
                type="primary"
                icon={<ImageIcon size={16} />}
                loading={busy}
                onClick={() => void extract()}
              >
                {t('assets.extract')}
              </Button>
            </Empty>
          </Card>
        )}
        {job && (
          <Card size="small" className="vn-task-card">
            <Space className="vn-content-toolbar">
              <Typography.Text strong>{t(`assets.${job.phase}`)}</Typography.Text>

              {job.state === 'failed' && (
                <Button onClick={() => void extract()}>{t('ui.retry')}</Button>
              )}
            </Space>
            {running && (
              <>
                <TaskProgress
                  action={
                    <Button
                      size="small"
                      onClick={() =>
                        void contentAPI(`tasks/assets/${job.task_id}/cancel`, {})
                          .then(() => setRefresh((v) => v + 1))
                          .catch((e) => setError(messageOf(e)))
                      }
                    >
                      {t('ui.cancel')}
                    </Button>
                  }
                  percent={
                    job.total_bytes
                      ? Math.min(100, Math.floor((job.completed_bytes / job.total_bytes) * 100))
                      : 0
                  }
                  showInfo
                />
                <Typography.Text type="secondary">
                  {bytes(job.completed_bytes)} / {bytes(job.total_bytes)} · {job.completed_files} /{' '}
                  {job.total_files}
                </Typography.Text>
              </>
            )}
            {job.messages?.error && <Alert type="error" title={t(job.messages.error)} />}
          </Card>
        )}
        {!!warningCount && (
          <Collapse
            size="small"
            data-testid="asset-warnings"
            styles={{ header: { minHeight: 44, alignItems: 'center' } }}
            items={[
              {
                key: 'warnings',
                label: (
                  <Space>
                    <CircleAlert size={16} aria-hidden="true" />
                    {t('assets.warningSummary', { count: warningCount })}
                  </Space>
                ),
                children: warnings.length ? (
                  <ul style={{ overflowWrap: 'anywhere' }}>
                    {warnings.slice(0, 20).map((warning, i) => (
                      <li key={i}>{t(warning)}</li>
                    ))}
                  </ul>
                ) : (
                  <Typography.Text>{t('assets.warning', { count: warningCount })}</Typography.Text>
                ),
              },
            ]}
          />
        )}
        {!!info?.retained_files && (
          <Alert
            type="warning"
            title={t('assets.retained', {
              count: info.retained_files,
              size: bytes(info.retained_bytes ?? 0),
            })}
          />
        )}
        {info?.ready && route.view === 'home' && (
          <>
            <div className="vn-group-list">
              {(info.categories ?? categories).map((kind) => (
                <ContentRow
                  key={kind}
                  testId={`asset-category-${kind}`}
                  icon={<FileIcon kind={kind} />}
                  title={t(`assets.${kind}`)}
                  trailing={<Tag>{info.counts[kind] ?? 0}</Tag>}
                  onClick={() => browse(kind)}
                />
              ))}
            </div>
            <div className="vn-group-list">
              <ContentRow
                icon={<FileIcon kind="directory" />}
                title={t('assets.all')}
                onClick={() => browse('')}
              />
            </div>
            <div className="vn-content-toolbar">
              <Typography.Text type="secondary">
                {t('assets.originalSize', { size: bytes(info.size_bytes) })} ·{' '}
                {t('assets.updated', { date: date(info.updated_at) })}
              </Typography.Text>
              <Button
                icon={<RefreshCw size={16} />}
                disabled={running}
                loading={busy}
                onClick={() =>
                  modal.confirm({
                    title: t('assets.reextract'),
                    content: t('assets.reextractConfirm'),
                    onOk: extract,
                  })
                }
              >
                {t('assets.reextract')}
              </Button>
            </div>
          </>
        )}
        {info?.ready && route.view === 'browse' && (
          <>
            <ContentPath
              folder={folder}
              root={t(category ? `assets.${category}` : 'assets.title')}
              onFolder={(value) =>
                navigateContent(browseHash({ folder: value, page: '1', search: '' }))
              }
            />
            <Input.Search
              allowClear
              placeholder={t('assets.search')}
              aria-label={t('assets.search')}
              value={search}
              onChange={(e) =>
                navigateContent(browseHash({ search: e.target.value, page: '1' }), true)
              }
            />
            {listing.error && <Alert type="error" title={t(listing.error)} />}
            <div className="vn-group-list" aria-busy={listing.loading}>
              {listing.value?.entries.map((entry) => (
                <ContentRow
                  key={entry.id}
                  testId={`asset-row-${entry.id}`}
                  icon={
                    entry.directory ? (
                      <FileIcon kind="directory" />
                    ) : ['image', 'video'].includes(entry.kind) ? (
                      <AssetThumbnail
                        path={`${prefix}/${entry.id}/thumbnail`}
                        kind={entry.kind}
                        generation={info.generation ?? undefined}
                      />
                    ) : (
                      <FileIcon kind={entry.kind} />
                    )
                  }
                  title={entry.name}
                  subtitle={
                    entry.directory
                      ? t('assets.count', { count: entry.count ?? 0 })
                      : bytes(entry.size_bytes)
                  }
                  onClick={() =>
                    navigateContent(
                      entry.directory
                        ? browseHash({ folder: entry.path, page: '1' })
                        : contentHash(projectId, 'assets', 'item', entry.id, params),
                    )
                  }
                />
              ))}
            </div>
            {listing.value?.pagination.total === 0 && <Empty />}
            <Pagination
              current={page}
              total={listing.value?.pagination.total ?? 0}
              pageSize={100}
              showSizeChanger={false}
              onChange={(value) => navigateContent(browseHash({ page: String(value) }))}
            />
          </>
        )}
      </div>
    </section>
  );
}
