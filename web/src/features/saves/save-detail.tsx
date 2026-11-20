import { Alert, Button, Card, Descriptions, Empty, Skeleton, Space, Tag, Typography } from 'antd';
import { ArchiveRestore, Download, Save, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { contentAPI, contentURL, useContent } from '../../api/content-client.js';
import type { ArchivedSave } from '../../api/generated/types.gen.js';
import type { SaveBackup, SaveEntry, SaveListing } from '../../api/types.js';
import { allowDraftNavigation } from '../../app/content-navigation.js';
import { navigateContent } from '../../app/content-route.js';
import { ContentHeader, FileIcon } from '../../ui/content-ui.js';
import { bytes, date } from '../../ui/format.js';
import { messageOf, t } from '../../ui/preferences-store.js';
import { readSaveRevision, useSaveActions } from './save-actions.js';
import { SaveEditor } from './save-editor.js';

function isActiveSlot(save: SaveEntry | ArchivedSave): save is SaveEntry {
  return !save.archived && 'kind' in save;
}

export function SaveDetail({
  prefix,
  uid,
  listHash,
  listing,
  canEdit,
  refresh,
  onChanged,
}: {
  prefix: string;
  uid: string;
  listHash: string;
  listing: SaveListing | null;
  canEdit: boolean;
  refresh: number;
  onChanged: () => void;
}) {
  const path = `${prefix}/${encodeURIComponent(uid)}`;
  const [backupOpen, setBackupOpen] = useState(false);
  const [editorReset, resetEditor] = useState(0);
  const { busy, error, setError, run, confirm, isActive } = useSaveActions(onChanged);
  const detail = useContent<SaveEntry | ArchivedSave>(path, refresh);
  const selected = detail.value?.id === uid ? detail.value : null;
  const slot = selected && isActiveSlot(selected) ? selected : null;
  const showBackups = !!selected && (backupOpen || !slot);
  const backups = useContent<SaveBackup[]>(`${path}/backups`, refresh, { enabled: showBackups });
  const writable = !!selected?.writable && !!listing?.writable && !busy;

  async function remove(item: SaveEntry) {
    try {
      const revision = await readSaveRevision(path);
      if (!isActive()) return;
      confirm({
        title: t('saves.delete'),
        content: t('saves.deleteConfirm', { name: item.name }),
        okButtonProps: { danger: true },
        onOk: async () => {
          if (!(await allowDraftNavigation()) || !isActive()) return;
          const removed = await run(() => contentAPI(path, { revision }, 'DELETE'));
          if (removed && isActive()) navigateContent(listHash);
        },
      });
    } catch (error) {
      if (isActive()) setError(messageOf(error));
    }
  }

  function backup() {
    void run(async () => {
      const revision = await readSaveRevision(path);
      if (!isActive()) return;
      await contentAPI(`${path}/backups`, { revision, request_id: crypto.randomUUID() });
      if (isActive()) setBackupOpen(true);
    });
  }

  function restore(backupId: string) {
    confirm({
      title: t('saves.restore'),
      content: t('saves.restoreConfirm'),
      onOk: async () => {
        if (!(await allowDraftNavigation()) || !isActive()) return;
        const restored = await run(async () => {
          const revision = slot ? await readSaveRevision(path) : null;
          if (isActive())
            await contentAPI(`${path}/backups/${encodeURIComponent(backupId)}/restore`, {
              revision,
            });
        });
        if (restored && isActive()) resetEditor((value) => value + 1);
      },
    });
  }

  return (
    <div className="vn-content-page">
      <ContentHeader title={selected?.name ?? t('saves.title')} parent={listHash} />
      {(error || detail.error) && <Alert type="error" showIcon title={t(error ?? detail.error!)} />}
      {detail.loading && !selected && <Card loading />}
      {selected && (
        <>
          <Card className="vn-slot-detail" size="small">
            <div className="vn-save-summary">
              {slot?.thumbnail ? (
                <img src={contentURL(`${path}/screenshot`)} alt="" />
              ) : (
                <FileIcon kind="save" size={36} />
              )}
              <div>
                <Typography.Title level={3}>
                  {slot?.metadata._save_name || selected.name.replace(/\.save$/i, '')}
                </Typography.Title>
                <Typography.Text type="secondary">{selected.name}</Typography.Text>
                {slot && (
                  <small>
                    {date(slot.updated_at)} · {bytes(slot.size_bytes)}
                  </small>
                )}
                <Space wrap>
                  {slot?.kind && <Tag>{t(`saves.${slot.kind}`)}</Tag>}
                  {slot?.signature && <Tag>{t(`saves.signature${slot.signature}`)}</Tag>}
                  {slot && slot.copies > 1 && (
                    <Tag>{t('saves.copies', { count: slot.copies })}</Tag>
                  )}
                </Space>
              </div>
            </div>
          </Card>
          <Space wrap className="vn-slot-actions">
            {slot && (
              <Button
                icon={<Save size={16} />}
                disabled={!writable}
                aria-label={t('saves.backupNow')}
                title={t('saves.backupDiskNote')}
                onClick={backup}
              >
                {t('saves.backupAction')}
              </Button>
            )}
            {slot && (
              <Button icon={<Download size={16} />} href={contentURL(`${path}/content`)}>
                {t('saves.download')}
              </Button>
            )}
            <Button
              aria-label={t('saves.backups')}
              icon={<ArchiveRestore size={16} />}
              onClick={() => setBackupOpen((value) => !value)}
            >
              {t('saves.historyAction')}
            </Button>
            {slot && slot.kind !== 'persistent' && (
              <Button
                danger
                icon={<Trash2 size={16} />}
                disabled={!writable}
                onClick={() => void remove(slot)}
              >
                {t('saves.delete')}
              </Button>
            )}
          </Space>
          {slot?.sources && (
            <Descriptions
              column={1}
              size="small"
              bordered
              items={[
                {
                  key: 'source',
                  label: t('content.location'),
                  children: slot.sources.map((source) => (
                    <div className="vn-file-path" key={source}>
                      {source}
                    </div>
                  )),
                },
              ]}
            />
          )}
          {showBackups && (
            <Card title={t('saves.backups')} size="small" className="vn-backup-list">
              {backups.error && (
                <div className="vn-backup-state">
                  <Alert type="error" title={t(backups.error)} />
                </div>
              )}
              {backups.loading && !backups.value && (
                <div className="vn-backup-state">
                  <Skeleton title={false} paragraph={{ rows: 2 }} />
                </div>
              )}
              {!backups.error && backups.value?.length === 0 && <Empty />}
              <ul
                className="vn-backup-records"
                aria-label={t('saves.backups')}
                aria-busy={backups.loading}
              >
                {backups.value?.map((backup) => (
                  <li className="vn-backup-row" key={backup.id}>
                    <div className="vn-backup-info">
                      <div className="vn-backup-summary">
                        <Typography.Text strong>{date(backup.created_at)}</Typography.Text>
                        {backup.size_bytes != null && (
                          <Typography.Text type="secondary">
                            {bytes(backup.size_bytes)}
                          </Typography.Text>
                        )}
                      </div>
                      {backup.kind && (
                        <div className="vn-backup-tags">
                          <Tag variant="outlined">
                            {t(
                              backup.kind === 'manual' ? 'saves.backupManual' : 'saves.backupAuto',
                            )}
                          </Tag>
                          {[
                            'edit',
                            'upload',
                            'delete',
                            'restore',
                            'sign',
                            'migration',
                            'migration-undo',
                          ].includes(backup.kind) && (
                            <Tag variant="outlined">
                              {t(
                                ['migration', 'migration-undo'].includes(backup.kind)
                                  ? `migrationUI.reason.${backup.kind}`
                                  : `saves.reason.${backup.kind}`,
                              )}
                            </Tag>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="vn-backup-actions">
                      <Button
                        icon={<Download size={16} />}
                        href={contentURL(
                          `${path}/backups/${encodeURIComponent(backup.id)}/content`,
                        )}
                      >
                        {t('saves.download')}
                      </Button>
                      <Button
                        icon={<ArchiveRestore size={16} />}
                        aria-label={t('saves.restore')}
                        disabled={!writable}
                        onClick={() => restore(backup.id)}
                      >
                        {t('saves.restoreAction')}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {canEdit && slot && (
            <SaveEditor
              key={editorReset}
              path={path}
              item={slot}
              refresh={refresh}
              onSaved={onChanged}
            />
          )}
        </>
      )}
    </div>
  );
}
