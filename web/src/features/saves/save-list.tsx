import { Alert, Button, Empty, Input, Space, Typography } from 'antd';
import { ArchiveRestore, ArrowRightLeft, FileUp } from 'lucide-react';
import { useRef, useState } from 'react';
import { contentURL, uploadSave } from '../../api/content-client.js';
import type { SaveListing } from '../../api/types.js';
import { changedParams, contentHash, navigateContent } from '../../app/content-route.js';
import { ContentRow, FileIcon } from '../../ui/content-ui.js';
import { date } from '../../ui/format.js';
import { t } from '../../ui/preferences-store.js';
import { FilterLabel, Segmented } from '../../ui/segments.js';
import { TaskProgress } from '../../ui/task-progress.js';
import { readSaveRevision, useSaveActions } from './save-actions.js';

export function SaveList({
  projectId,
  prefix,
  params,
  listing,
  loading,
  canMigrate,
  onChanged,
}: {
  projectId: string;
  prefix: string;
  params: URLSearchParams;
  listing: SaveListing | null;
  loading: boolean;
  canMigrate: boolean;
  onChanged: () => void;
}) {
  const [progress, setProgress] = useState<number | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const globalFile = useRef<HTMLInputElement>(null);
  const { busy, error, setError, run, confirm, isActive } = useSaveActions(onChanged);
  const search = params.get('search') ?? '';
  const kind = params.get('kind') ?? '';
  const searchText = search.toLocaleLowerCase();
  const items = (listing?.saves ?? [])
    .filter(
      (item) =>
        (!kind || item.kind === kind) &&
        (!searchText ||
          `${item.name} ${item.metadata._save_name ?? ''}`
            .toLocaleLowerCase()
            .includes(searchText)),
    )
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  const activeIds = new Set(listing?.saves.map((item) => item.id));
  const archived = listing?.archived?.filter((item) => !activeIds.has(item.id)) ?? [];

  async function upload(input: HTMLInputElement, global = false) {
    const value = input.files?.[0];
    input.value = '';
    if (!value) return;
    if (value.size > 64 * 1024 ** 2) {
      setError('saves.tooLarge');
      return;
    }
    const name = global ? 'persistent' : value.name;
    const existing = listing?.saves.find((item) => item.name === name);
    const perform = () =>
      run(async () => {
        const revision = existing ? await readSaveRevision(`${prefix}/${existing.id}`) : '';
        if (!isActive()) return;
        setProgress(0);
        try {
          await uploadSave(
            `${prefix}/upload?${new URLSearchParams({ name, ...(revision ? { revision } : {}) })}`,
            value,
            setProgress,
          );
        } finally {
          setProgress(null);
        }
      });
    if (existing)
      confirm({
        title: t('saves.upload'),
        content: t('saves.replaceConfirm', { name }),
        onOk: perform,
      });
    else await perform();
  }

  return (
    <div className="vn-content-page">
      {error && <Alert type="error" showIcon title={t(error)} />}
      <input
        hidden
        type="file"
        accept=".save"
        ref={file}
        onChange={(event) => void upload(event.currentTarget)}
      />
      <input
        hidden
        type="file"
        ref={globalFile}
        onChange={(event) => void upload(event.currentTarget, true)}
      />
      {progress !== null && <TaskProgress percent={progress} label={t('saves.uploading')} />}
      <div className="vn-content-toolbar">
        <Typography.Title level={3}>{t('saves.title')}</Typography.Title>
        <Space wrap>
          {canMigrate && (
            <Button
              icon={<ArrowRightLeft size={16} />}
              aria-label={t('migrationUI.title')}
              title={t('migrationUI.title')}
              onClick={() =>
                navigateContent(contentHash(projectId, 'saves', 'migrate', '', params))
              }
            />
          )}
          <Button
            icon={<FileUp size={16} />}
            disabled={!listing?.writable || busy}
            onClick={() => file.current?.click()}
          >
            {t('saves.upload')}
          </Button>
          <Button
            icon={<FileUp size={16} />}
            disabled={!listing?.writable || busy}
            onClick={() => globalFile.current?.click()}
          >
            {t('saves.uploadGlobal')}
          </Button>
        </Space>
      </div>
      <Input.Search
        value={search}
        allowClear
        aria-label={t('saves.searchSlots')}
        placeholder={t('saves.searchSlots')}
        onChange={(e) =>
          navigateContent(
            contentHash(
              projectId,
              'saves',
              '',
              '',
              changedParams(params, { search: e.target.value }),
            ),
            true,
          )
        }
      />
      <div className="vn-slot-filters">
        <Segmented
          pills
          aria-label={t('saves.title')}
          value={kind}
          onChange={(v) =>
            navigateContent(
              contentHash(projectId, 'saves', '', '', changedParams(params, { kind: String(v) })),
              true,
            )
          }
          options={['', 'manual', 'auto', 'quick', 'persistent'].map((value) => ({
            value,
            label: (
              <FilterLabel
                label={t(value ? `saves.${value}` : 'saves.all')}
                count={listing?.saves.filter((item) => !value || item.kind === value).length ?? 0}
              />
            ),
          }))}
        />
      </div>
      <div className="vn-group-list" aria-busy={busy || loading}>
        {items.map((item) => (
          <ContentRow
            key={item.id}
            testId={`save-row-${item.id}`}
            icon={
              item.thumbnail ? (
                <img
                  className="vn-slot-thumbnail"
                  loading="lazy"
                  src={contentURL(`${prefix}/${item.id}/screenshot`)}
                  alt=""
                />
              ) : (
                <span className="vn-slot-thumbnail vn-slot-placeholder">
                  <FileIcon kind="save" />
                </span>
              )
            }
            title={String(item.metadata._save_name || item.name.replace(/\.save$/i, ''))}
            subtitle={
              <>
                {date(item.updated_at)} · {t(`saves.${item.kind}`)}
              </>
            }
            onClick={() =>
              navigateContent(contentHash(projectId, 'saves', 'item', item.id, params))
            }
          />
        ))}
      </div>
      {!items.length && !loading && <Empty description={t('saves.empty')} />}
      {archived.length > 0 && (
        <>
          <Typography.Title level={4}>{t('saves.backups')}</Typography.Title>
          <div className="vn-group-list">
            {archived.map((item) => (
              <ContentRow
                key={item.id}
                icon={<ArchiveRestore size={24} />}
                title={item.name}
                onClick={() =>
                  navigateContent(contentHash(projectId, 'saves', 'item', item.id, params))
                }
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
