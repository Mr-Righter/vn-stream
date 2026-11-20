import { TaskProgress } from '../../ui/task-progress.js';
import { Segmented } from '../../ui/segments.js';
import { useEffect, useRef, useSyncExternalStore } from 'react';
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { Archive, Folder, Upload, X } from 'lucide-react';
import type { Engine } from '../../api/types.js';
import { t } from '../../ui/preferences-store.js';
import { bytes } from '../../ui/format.js';
import { usePreferences } from '../../ui/provider.js';
import { ImportController, supported, type SourceTab } from './import-controller.js';

export function AddPage({
  controller,
  hidden,
  engines,
}: {
  controller: ImportController;
  hidden: boolean;
  engines: Engine[];
}) {
  usePreferences();
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot);
  const {
    tab,
    file,
    upload,
    source,
    root,
    path,
    job,
    busy,
    cancelling,
    starting,
    restoring,
    pending,
    listing,
  } = state;
  const directory = controller.directory;
  const archiveInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!file && archiveInput.current) archiveInput.current.value = '';
  }, [file]);
  const locked = controller.locked;
  const folderLocked = restoring || busy || cancelling || !!job || pending || !directory.supported;
  const retry = job?.state === 'failed';
  const ready =
    job || pending || (tab === 'upload' ? file : tab === 'folder' ? directory.ready : source);
  const folderUpload = !!job?.directory_upload_id || (!job && directory.active);
  const uploadVisible = !!upload || !!job?.upload_id || folderUpload;
  const total = file?.size ?? (job?.upload_id ? 1 : 0);
  const current = job?.upload_id ? total : state.uploadedBytes;
  const uploadPercent = folderUpload
    ? job
      ? 100
      : directory.total
        ? (directory.sentBytes / directory.total) * 100
        : directory.snapshot?.state === 'complete'
          ? 100
          : 0
    : total
      ? (current / total) * 100
      : 0;
  const determinate = job?.phase === 'extracting' && !!job.total_bytes;
  const processingPercent = determinate
    ? Math.min(100, (job!.completed_bytes / job!.total_bytes!) * 100)
    : job?.state === 'completed'
      ? 100
      : 0;
  const phases: Record<string, string> = {
    scanning: 'ui.scanning',
    validating: 'ui.validatingFolder',
    extracting: 'ui.extracting',
    normalizing: 'ui.preparingGame',
    registering: 'ui.registeringGame',
    signing: 'saves.signing',
    cleaning: 'ui.cleaningSource',
    completed: 'ui.gameAdded',
    failed: job?.directory_upload_id
      ? 'ui.folderImportFailedReviewTheErrorAndRetry'
      : 'ui.importFailedCheckThePasswordAndRetry',
  };
  const passwordHidden =
    !!job?.directory_upload_id ||
    (!job && tab === 'folder') ||
    (!!job && !retry) ||
    (!job && tab === 'server' && !!source?.directory);
  const selectedName =
    job?.source_name ??
    (tab === 'upload' ? file?.name : tab === 'folder' ? directory.name : source?.name);
  const hint =
    state.status || job?.messages?.error || job?.error || job?.messages?.warning || job?.warning;
  const parts = ['', ...path.split('/').filter(Boolean)];
  const cancelUpload = (
    <Button
      id="cancel-upload"
      icon={<X size={16} />}
      hidden={(!upload && !directory.active) || !!job}
      disabled={cancelling || starting}
      onClick={() => void controller.cancel()}
    >
      {t('ui.cancelUpload')}
    </Button>
  );
  return (
    <div id="add-page" hidden={hidden}>
      <Typography.Paragraph type="secondary">
        {t('ui.chooseAnArchiveOrGameFolder')}
      </Typography.Paragraph>
      <Space className="format-tags" size={[8, 8]} wrap>
        {['ZIP', '7Z', 'RAR', 'TAR', 'TAR.GZ', 'TAR.BZ2', 'TAR.XZ', 'APK'].map((format) => (
          <Tag key={format} variant="outlined">
            {format}
          </Tag>
        ))}
      </Space>
      <Space wrap className="vn-import-engine">
        <Typography.Text>{t('engine.detectAs')}</Typography.Text>
        <Select
          aria-label={t('engine.detectAs')}
          value={state.engineId}
          disabled={busy || starting || cancelling || pending || (!!job && !retry)}
          onChange={controller.setEngineId}
          style={{ minWidth: 160 }}
          options={[
            { value: '', label: t('engine.auto') },
            ...engines.map((engine) => ({ value: engine.engine_id, label: engine.display_name })),
          ]}
        />
      </Space>
      <Segmented
        id="source-tabs"
        block
        size="large"
        value={tab}
        aria-label={t('ui.addGame')}
        options={(['upload', 'folder', 'server'] as const).map((value, i) => ({
          value,
          label: t(['ui.uploadFile', 'ui.uploadFolder', 'ui.serverFiles'][i]),
          disabled: locked && value !== tab,
        }))}
        onChange={(value) => controller.selectTab(value as SourceTab)}
      />
      <Card className="import-card">
        <div id="upload-source" hidden={tab !== 'upload'} className="vn-source-panel">
          <input
            ref={archiveInput}
            id="archive-file"
            type="file"
            accept=".zip,.7z,.rar,.tar,.gz,.bz2,.xz,.apk"
            hidden
            aria-label={t('ui.chooseFile')}
            disabled={locked}
            onChange={(event) => controller.chooseFile(event.target.files?.[0] ?? null)}
          />
          <Button
            id="choose-file"
            size="large"
            icon={<Upload size={18} />}
            block
            disabled={locked}
            onClick={() => archiveInput.current?.click()}
          >
            {t('ui.chooseFile')}
          </Button>
          <Typography.Text type="secondary">
            {t('ui.uploadsAreExtractedAndAddedAutomatically')}
          </Typography.Text>
        </div>
        <div id="folder-source" hidden={tab !== 'folder'} className="vn-source-panel">
          <input
            ref={folderInput}
            id="folder-file"
            type="file"
            hidden
            {...{ webkitdirectory: '' }}
            aria-label={t('ui.chooseFolder')}
            disabled={folderLocked}
            onChange={(event) => controller.chooseFolder(event.target.files)}
          />
          <Button
            id="choose-folder"
            size="large"
            icon={<Folder size={18} />}
            block
            disabled={folderLocked}
            onClick={() => {
              if (folderInput.current) {
                folderInput.current.value = '';
                folderInput.current.click();
              }
            }}
          >
            {t('ui.chooseFolder')}
          </Button>
          <Typography.Text type="secondary">
            {t('ui.keepsTheCompleteProjectAndExistingSavesBrowsersCannot')}
          </Typography.Text>
          {!directory.supported && (
            <Alert
              id="folder-unsupported"
              title={t('ui.thisBrowserCannotSelectFoldersUploadAnArchiveInstead')}
              type="warning"
            />
          )}
          {directory.needsSelection && (
            <Alert
              id="folder-resume"
              title={t('ui.reselectTheSameOriginalFolderToContinueUploading')}
              type="info"
            />
          )}
          {!directory.storageAvailable && (
            <Alert
              id="folder-storage"
              title={t('ui.thisBrowserCannotSaveALocalRecordReopenThe')}
              type="warning"
            />
          )}
        </div>
        <div id="server-source" hidden={tab !== 'server'} className="vn-source-panel">
          {state.browseLoading && <Spin aria-label={t('ui.loadingFiles')} />}
          {state.browseError && <Alert title={t(state.browseError)} type="error" showIcon />}
          {listing && (
            <>
              <Select
                id="source-roots"
                aria-label={t('ui.chooseLocation')}
                placeholder={t('ui.chooseLocation')}
                value={root === undefined ? undefined : String(root)}
                disabled={locked}
                style={{ width: '100%' }}
                options={listing.roots.map((item) => ({
                  value: String(item.id),
                  label: item.name,
                }))}
                onChange={(value) =>
                  controller.chooseRoot(value === '' ? undefined : Number(value))
                }
              />
              {!listing.roots.length && (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={t('ui.noServerFoldersAvailable')}
                />
              )}
              {root !== undefined && (
                <>
                  <Breadcrumb
                    className="browser-path"
                    aria-label={t('ui.currentFolder')}
                    items={parts.map((part, index) => ({
                      title: (
                        <Button
                          size="small"
                          type="text"
                          disabled={locked}
                          onClick={() => controller.browsePath(parts.slice(1, index + 1).join('/'))}
                        >
                          {part || '/'}
                        </Button>
                      ),
                    }))}
                  />
                  {listing.project && listing.source && (
                    <Button
                      id="select-directory"
                      icon={<Folder size={16} />}
                      disabled={locked}
                      onClick={() => controller.selectCurrent()}
                    >
                      {t('ui.importThisGameFolder')}
                    </Button>
                  )}
                  <div className="file-list" aria-label={t('ui.serverFiles')}>
                    {listing.entries.map((entry) => (
                      <Button
                        key={entry.path}
                        className="file-entry"
                        icon={entry.directory ? <Folder size={18} /> : <Archive size={18} />}
                        disabled={locked || (!entry.directory && !supported.test(entry.name))}
                        onClick={() => controller.selectEntry(entry)}
                      >
                        {entry.name}
                      </Button>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
        <div id="selected-source" className="selected-source" hidden={!selectedName}>
          <Typography.Text strong>{selectedName}</Typography.Text>
          {!job && tab === 'folder' && (
            <Typography.Text type="secondary">
              {' '}
              · {directory.count} {t('ui.files')} · {bytes(directory.total)}
            </Typography.Text>
          )}
          {!job && file && tab === 'upload' && (
            <Typography.Text type="secondary"> · {bytes(file.size)}</Typography.Text>
          )}
          {job && !job.directory_upload_id && (
            <Typography.Paragraph type="secondary">
              {t(job.delete_source ? 'ui.deleteServerSourceAfterSuccess' : 'ui.keepServerSource')}
            </Typography.Paragraph>
          )}
        </div>
        <label id="password-label" className="vn-field" hidden={passwordHidden}>
          <span>{t('ui.archivePasswordOptional')}</span>
          <Input.Password
            id="archive-password"
            aria-label={t('ui.archivePasswordOptional')}
            autoComplete="off"
            value={state.password}
            disabled={busy}
            onChange={(event) => controller.setPassword(event.target.value)}
          />
        </label>
        <div hidden={!!job || tab !== 'server' || !source || source.directory}>
          <Checkbox
            id="delete-source"
            checked={state.deleteSource}
            disabled={locked}
            onChange={(event) => controller.setDeleteSource(event.target.checked)}
          >
            {t('ui.deleteServerSourceAfterSuccess')}
          </Checkbox>
        </div>
        <Space wrap className="import-actions">
          <Button
            id="start-import"
            type="primary"
            icon={<Archive size={16} />}
            hidden={!!job && !retry}
            disabled={restoring || busy || cancelling || !ready}
            onClick={() => void controller.startImport()}
          >
            {t(retry ? 'ui.retryImport' : pending && !busy ? 'ui.retryRequest' : 'ui.import')}
          </Button>

          <Button
            id="abandon-job"
            icon={<X size={16} />}
            hidden={!retry}
            disabled={busy}
            onClick={() => void controller.abandon()}
          >
            {t('ui.abandonTask')}
          </Button>
        </Space>
      </Card>
      <div id="import-progress" className="vn-task-progress">
        <Card id="upload-progress-section" hidden={!uploadVisible}>
          <Typography.Title level={3}>{t('ui.uploading')}</Typography.Title>
          <div id="upload-progress">
            <TaskProgress
              label={t('ui.uploading')}
              action={cancelUpload}
              percent={Math.min(100, uploadPercent)}
              status={busy ? 'active' : 'normal'}
            />
          </div>
          <div id="upload-detail">
            {folderUpload
              ? job
                ? t('ui.uploadComplete')
                : t('imports.folderProgress', {
                    confirmed: bytes(directory.confirmed),
                    total: bytes(directory.total),
                    sent: bytes(directory.sentBytes),
                  })
              : job?.upload_id
                ? t('ui.uploadComplete')
                : `${bytes(current)} / ${bytes(total)} · ${total ? Math.floor((current / total) * 100) : 0}%`}
          </div>
        </Card>
        <Card id="processing-progress-section" hidden={!job && !starting}>
          <Typography.Title level={3}>{t('ui.processing')}</Typography.Title>
          <div
            id="processing-progress"
            data-indeterminate={!determinate && job?.state !== 'completed'}
          >
            <TaskProgress
              label={t('ui.processing')}
              percent={processingPercent}
              showInfo={determinate || job?.state === 'completed'}
              status={
                job?.state === 'failed'
                  ? 'exception'
                  : job?.state === 'running' || starting
                    ? 'active'
                    : 'normal'
              }
            />
          </div>
          <div id="processing-detail">
            {job
              ? `${t(phases[job.phase] ?? 'ui.preparing')}${determinate ? ` · ${bytes(job.completed_bytes)} / ${bytes(job.total_bytes!)} · ${Math.floor((job.completed_bytes / job.total_bytes!) * 100)}%` : ''}`
              : t('ui.preparing')}
          </div>
        </Card>
        <div id="import-status" role="status" aria-live="polite">
          {hint && (
            <Alert
              title={t(hint)}
              type={state.status || job?.error ? 'error' : 'warning'}
              showIcon
            />
          )}
        </div>
        <div id="connection-status" role="status">
          {state.connection && <Alert title={t(state.connection)} type="warning" showIcon />}
        </div>
      </div>
    </div>
  );
}
