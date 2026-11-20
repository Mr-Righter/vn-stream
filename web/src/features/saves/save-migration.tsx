import type {
  MigrationProject,
  MigrationSource,
  MigrationPreview,
  MigrationTask,
} from '../../api/types.js';
import { useEffect, useRef, useState, type RefObject } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  Modal,
  Select,
  Skeleton,
  Tag,
  Typography,
} from 'antd';
import { ArrowRightLeft, ChevronDown, RefreshCw, Search, Undo2 } from 'lucide-react';
import { t, messageOf, type MessageInput } from '../../ui/preferences-store.js';
import { bytes, date } from '../../ui/format.js';
import { TaskProgress } from '../../ui/task-progress.js';
import { contentAPI, useContent } from '../../api/content-client.js';
import { contentHash, navigateContent } from '../../app/content-route.js';
import { useViewLifetime } from './save-actions.js';
import { ContentHeader, ContentRow } from '../../ui/content-ui.js';

type Metadata = MigrationProject;
type Source = MigrationSource;
type Preview = MigrationPreview;
type Task = MigrationTask;
const terminal = (task: Task) => ['completed', 'failed'].includes(task.state);
const phaseKeys = [
  'queued',
  'running',
  'validating',
  'preparing',
  'backing_up',
  'writing',
  'rolling_back',
  'completed',
  'failed',
  'recovering',
];
const resultKeys = ['pending', 'skipped', 'completed', 'rolled_back', 'not_written', 'recovering'];
const kindLabel = (kind: string) =>
  t(['manual', 'auto', 'quick', 'persistent'].includes(kind) ? `saves.${kind}` : 'saves.title');
const actionLabel = (action: string) =>
  t(
    ['add', 'overwrite', 'remove'].includes(action)
      ? `migrationUI.action.${action}`
      : 'migrationUI.files',
  );
function Project({ project }: { project: Metadata }) {
  return (
    <div className="vn-migration-project">
      <Typography.Text strong>{project.display_name || project.name}</Typography.Text>
      <Typography.Text type="secondary">
        {t('migrationUI.versions', {
          release: project.release_version ?? t('ui.unknownVersion'),
          engine: project.engine_version ?? t('ui.unknownVersion'),
        })}
      </Typography.Text>
    </div>
  );
}
function PreviewDetails({ preview }: { preview: Preview }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="vn-content-page">
      <div className="vn-migration-projects">
        <div>
          <small>{t('migrationUI.source')}</small>
          <Project project={preview.source} />
        </div>
        <ArrowRightLeft size={18} aria-hidden="true" />
        <div>
          <small>{t('migrationUI.target')}</small>
          <Project project={preview.target} />
        </div>
      </div>
      <div className="vn-migration-tags">
        {(['manual', 'auto', 'quick', 'persistent'] as const).map((kind) => (
          <Tag key={kind}>
            {kindLabel(kind)} · {preview.entries.filter((entry) => entry.kind === kind).length}
          </Tag>
        ))}
      </div>
      <Typography.Text>
        {t('migrationUI.summary', {
          total: preview.counts.total,
          valid: preview.counts.valid,
          invalid: preview.counts.invalid,
        })}
      </Typography.Text>
      <Typography.Text>
        {t('migrationUI.selectedBytes', {
          bytes: bytes(
            preview.entries
              .filter((entry) => entry.valid)
              .reduce((total, entry) => total + entry.size_bytes, 0),
          ),
        })}
      </Typography.Text>
      <Typography.Text type="secondary">
        {t('migrationUI.requiredBytes', { bytes: bytes(preview.required_bytes) })}
      </Typography.Text>
      <Typography.Text type="secondary">
        {t('migrationUI.changes', {
          add: preview.counts.add,
          overwrite: preview.counts.overwrite,
          remove: preview.entries.filter((entry) => entry.action === 'remove').length,
        })}
      </Typography.Text>
      {preview.warnings.map((warning, index) => (
        <Alert key={index} type="warning" showIcon title={t(warning)} />
      ))}
      <Button
        aria-label={t('migrationUI.files')}
        icon={<ChevronDown size={16} />}
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {t('migrationUI.filesShort')}
      </Button>
      {expanded && (
        <ul className="vn-migration-files" aria-label={t('migrationUI.files')}>
          {preview.entries.map((entry) => (
            <li key={entry.name}>
              <Typography.Text strong>{entry.name}</Typography.Text>
              <div className="vn-migration-tags">
                <Tag>{kindLabel(entry.kind)}</Tag>
                <Tag color={entry.action === 'overwrite' ? 'warning' : undefined}>
                  {actionLabel(entry.action)}
                </Tag>
                <Typography.Text type="secondary">
                  {bytes(entry.size_bytes)} · {date(entry.updated_at)}
                </Typography.Text>
              </div>
              <Typography.Text type="secondary">
                {t('migrationUI.copies', {
                  source: entry.source_copies,
                  target: entry.target_copies,
                })}
              </Typography.Text>
              {!entry.valid && (
                <Alert type="warning" showIcon title={t(entry.reason ?? 'migrationUI.invalid')} />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
function TaskResults({ task }: { task: Task }) {
  const [expanded, setExpanded] = useState(false);
  const percent =
    task.state === 'completed'
      ? 100
      : task.total
        ? Math.min(100, Math.floor((task.completed * 100) / task.total))
        : 0;
  return (
    <Card
      size="small"
      data-testid="migration-task"
      className="vn-migration-task"
      aria-live="polite"
    >
      <div className="vn-content-page">
        <Typography.Title level={4}>
          {t(task.kind === 'undo' ? 'migrationUI.undoTask' : 'migrationUI.task')}
        </Typography.Title>
        <div className="vn-migration-projects">
          <div>
            <small>{t('migrationUI.source')}</small>
            <Project project={task.source} />
          </div>
          <div>
            <small>{t('migrationUI.target')}</small>
            <Project project={task.target} />
          </div>
        </div>
        <Tag>
          {t(
            phaseKeys.includes(task.phase)
              ? `migrationUI.phase.${task.phase}`
              : 'migrationUI.phase.recovering',
          )}
        </Tag>
        <TaskProgress
          percent={percent}
          label={t('migrationUI.progress')}
          status={
            task.state === 'failed'
              ? 'exception'
              : task.state === 'completed'
                ? 'success'
                : 'active'
          }
        />
        <Typography.Text>
          {t('migrationUI.taskCounts', {
            completed: task.completed,
            total: task.total,
            skipped: task.skipped,
          })}
        </Typography.Text>
        <Typography.Text type="secondary">
          {t('migrationUI.prepared', { prepared: task.prepared, backups: task.backed_up })}
        </Typography.Text>
        {!terminal(task) && <Alert type="info" showIcon title={t('migrationUI.continues')} />}
        {task.error && (
          <Alert type="error" showIcon title={t(task.messages?.error ?? messageOf(task.error))} />
        )}
        <Button
          aria-label={t('migrationUI.results')}
          icon={<ChevronDown size={16} />}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {t('migrationUI.resultsShort')}
        </Button>
        {expanded && (
          <ul className="vn-migration-files" aria-label={t('migrationUI.results')}>
            {task.results.map((result) => (
              <li key={result.name}>
                <Typography.Text strong>{result.name}</Typography.Text>
                <div className="vn-migration-tags">
                  <Tag>{actionLabel(result.action)}</Tag>
                  <Tag>
                    {t(
                      resultKeys.includes(result.state)
                        ? `migrationUI.result.${result.state}`
                        : 'migrationUI.result.recovering',
                    )}
                  </Tag>
                </div>
                {result.reason && <Typography.Text>{t(result.reason)}</Typography.Text>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

type History = Pick<ReturnType<typeof useContent<Task[]>>, 'value' | 'error' | 'loading'>;
type TaskFlight = RefObject<Promise<void> | null>;
type Operation = 'preview' | 'submit' | 'undo-preview' | 'undo';

type MigrationPageProps = {
  projectId: string;
  prefix: string;
  params: URLSearchParams;
  history: History;
  reloadHistory: () => void;
  taskFlight: TaskFlight;
  onChanged: () => void;
};

export function SaveMigration({
  projectId,
  prefix,
  params,
  history,
  reloadHistory,
  taskFlight,
  onChanged,
}: MigrationPageProps) {
  const taskId = params.get('task') || null;
  const listParams = new URLSearchParams(params);
  listParams.delete('task');
  const listParent = contentHash(projectId, 'saves', '', '', listParams);
  const [submitted, setSubmitted] = useState<Task | null>(null);
  function openTask(id: string | null, replace = false) {
    const next = new URLSearchParams(listParams);
    if (id) next.set('task', id);
    navigateContent(contentHash(projectId, 'saves', 'migrate', '', next), replace);
  }

  return (
    <section className="vn-content-page vn-migration-page" aria-label={t('migrationUI.title')}>
      <ContentHeader title={t('migrationUI.title')} parent={listParent} />
      <MigrationView
        key={taskId === null ? 'form' : `task:${taskId}`}
        projectId={projectId}
        prefix={prefix}
        taskId={taskId}
        submitted={submitted?.task_id === taskId ? submitted : null}
        history={history}
        reloadHistory={reloadHistory}
        taskFlight={taskFlight}
        onChanged={onChanged}
        onTaskSubmitted={(task) => {
          setSubmitted(task);
          openTask(task.task_id, true);
        }}
        openTask={openTask}
      />
    </section>
  );
}

function MigrationView({
  projectId,
  prefix,
  taskId,
  submitted,
  history,
  reloadHistory,
  taskFlight,
  onChanged,
  onTaskSubmitted,
  openTask,
}: Omit<MigrationPageProps, 'params'> & {
  taskId: string | null;
  submitted: Task | null;
  onTaskSubmitted: (task: Task) => void;
  openTask: (id: string | null) => void;
}) {
  const [operation, setOperation] = useState<Operation | null>(null);
  const [error, setError] = useState<MessageInput | null>(null);
  const [undo, setUndo] = useState<Preview | null>(null);
  const [undoError, setUndoError] = useState<MessageInput | null>(null);
  const [retry, setRetry] = useState(0);
  const inProgress = useRef(false);
  const requests = useRef(new Map<string, string>());
  const isActive = useViewLifetime();

  async function run<T>(action: Operation, request: () => Promise<T>, receive: (value: T) => void) {
    if (inProgress.current || !isActive()) return;
    inProgress.current = true;
    setOperation(action);
    setError(null);
    setUndoError(null);
    try {
      const value = await request();
      if (isActive()) receive(value);
    } catch (error) {
      if (isActive()) (action === 'undo' ? setUndoError : setError)(messageOf(error));
    } finally {
      inProgress.current = false;
      if (action === 'submit' || action === 'undo') onChanged();
      if (isActive()) setOperation(null);
    }
  }

  function commit(preview: Preview, skipInvalid = false) {
    const undoing = preview.kind === 'undo';
    const key = `${preview.id}:${skipInvalid}`;
    const requestId = requests.current.get(key) ?? crypto.randomUUID();
    requests.current.set(key, requestId);
    return run(
      undoing ? 'undo' : 'submit',
      async () => {
        const task = await contentAPI<Task>(
          undoing
            ? `tasks/save-migrations/${encodeURIComponent(preview.migration_id!)}/undo`
            : `${prefix}/migrations`,
          {
            preview_id: preview.id,
            request_id: requestId,
            ...(!undoing ? { skip_invalid: skipInvalid } : {}),
          },
        );
        if (task.target_id !== projectId) throw new Error('Unexpected migration target');
        return task;
      },
      onTaskSubmitted,
    );
  }

  function previewUndo(id: string) {
    return run(
      'undo-preview',
      async () => {
        const value = await contentAPI<Preview>(
          `tasks/save-migrations/${encodeURIComponent(id)}/undo-preview`,
          {},
        );
        if (value.target.project_id !== projectId || value.migration_id !== id)
          throw new Error('Unexpected undo preview');
        return value;
      },
      setUndo,
    );
  }

  const retryView = () => {
    setError(null);
    setRetry((value) => value + 1);
    reloadHistory();
  };

  return (
    <>
      {error && <MigrationError error={error} retry={retryView} />}
      {taskId === null ? (
        <MigrationForm
          projectId={projectId}
          prefix={prefix}
          operation={operation}
          run={run}
          commit={commit}
          clearError={() => setError(null)}
          retry={retry}
          retryView={retryView}
        />
      ) : (
        <MigrationProgress
          projectId={projectId}
          taskId={taskId}
          submitted={submitted}
          taskFlight={taskFlight}
          onChanged={onChanged}
          onNew={() => openTask(null)}
        />
      )}
      <MigrationHistory
        history={history}
        busy={operation !== null}
        retry={reloadHistory}
        openTask={openTask}
        previewUndo={(id) => void previewUndo(id)}
      />
      {undo && (
        <UndoConfirmation
          undo={undo}
          error={undoError}
          busy={operation === 'undo'}
          onCancel={() => setUndo(null)}
          onConfirm={() => void commit(undo)}
        />
      )}
    </>
  );
}

function MigrationError({ error, retry }: { error: MessageInput; retry: () => void }) {
  return (
    <Alert
      type="error"
      showIcon
      title={t(error)}
      action={
        <Button icon={<RefreshCw size={16} />} onClick={retry}>
          {t('migrationUI.retry')}
        </Button>
      }
    />
  );
}

function MigrationProgress({
  projectId,
  taskId,
  submitted,
  taskFlight,
  onChanged,
  onNew,
}: {
  projectId: string;
  taskId: string;
  submitted: Task | null;
  taskFlight: TaskFlight;
  onChanged: () => void;
  onNew: () => void;
}) {
  const [task, setTask] = useState(submitted);
  const [error, setError] = useState<MessageInput | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      // A new task view waits for the previous status request to settle.
      while (taskFlight.current) await taskFlight.current;
      if (!alive) return;
      const request = (async () => {
        try {
          const value = await contentAPI<Task>(
            `tasks/save-migrations/${encodeURIComponent(taskId)}`,
          );
          if (!alive) return;
          if (value.target_id !== projectId || value.task_id !== taskId) {
            setError('common.requestFailed');
            return;
          }
          setTask(value);
          if (terminal(value)) onChanged();
          else timer = setTimeout(() => void poll(), 1200);
        } catch (error) {
          if (alive) setError(messageOf(error));
        }
      })();
      taskFlight.current = request;
      await request;
      if (taskFlight.current === request) taskFlight.current = null;
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [projectId, taskId, retry, taskFlight, onChanged]);

  return (
    <>
      {error && (
        <MigrationError
          error={error}
          retry={() => {
            setError(null);
            setRetry((value) => value + 1);
          }}
        />
      )}
      {!task && !error && <Card loading aria-busy="true" />}
      {task && <TaskResults task={task} />}
      <div className="vn-migration-actions">
        <Button aria-label={t('migrationUI.new')} onClick={onNew}>
          {t('migrationUI.newShort')}
        </Button>
      </div>
    </>
  );
}

function MigrationForm({
  projectId,
  prefix,
  operation,
  run,
  commit,
  clearError,
  retry,
  retryView,
}: {
  projectId: string;
  prefix: string;
  operation: Operation | null;
  run: <T>(
    operation: Operation,
    request: () => Promise<T>,
    receive: (value: T) => void,
  ) => Promise<void>;
  commit: (preview: Preview, skipInvalid: boolean) => Promise<void>;
  clearError: () => void;
  retry: number;
  retryView: () => void;
}) {
  const [source, setSource] = useState<string>();
  const [sourceQuery, setSourceQuery] = useState('');
  const [includePersistent, setIncludePersistent] = useState(true);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [skip, setSkip] = useState(false);
  const sources = useContent<Source[]>(`${prefix}/migration-sources`, retry);
  const byId = new Map(sources.value?.map((item) => [item.project_id, item]));
  const selected = source ? byId.get(source) : undefined;
  const busy = operation !== null;

  function changeSource(value: string | undefined) {
    setSource(value);
    setPreview(null);
    setSkip(false);
    clearError();
  }

  function loadPreview() {
    if (!selected?.available || busy) return;
    void run(
      'preview',
      async () => {
        const value = await contentAPI<Preview>(`${prefix}/migrations/preview`, {
          source_id: selected.project_id,
          include_persistent: includePersistent,
        });
        if (
          value.source.project_id !== selected.project_id ||
          value.target.project_id !== projectId
        )
          throw new Error('Unexpected migration preview');
        return value;
      },
      (value) => {
        setPreview(value);
        setSkip(false);
      },
    );
  }

  return (
    <>
      {sources.error && <MigrationError error={sources.error} retry={retryView} />}
      <>
        <Alert type="warning" showIcon title={t('migration.compatibility')} />
        <Card size="small" aria-busy={sources.loading || busy}>
          <div className="vn-content-page">
            <Typography.Title level={4}>{t('migrationUI.select')}</Typography.Title>
            <label htmlFor={`migration-source-${projectId}`}>{t('migrationUI.source')}</label>
            <Input
              size="large"
              type="search"
              autoComplete="off"
              name="migration-source-search"
              aria-label={t('migrationUI.searchSources')}
              placeholder={t('migrationUI.searchSources')}
              prefix={<Search size={16} aria-hidden="true" />}
              value={sourceQuery}
              disabled={busy}
              onChange={(event) => setSourceQuery(event.target.value)}
            />
            <Select
              size="large"
              classNames={{ popup: { root: 'vn-migration-source-popup' } }}
              id={`migration-source-${projectId}`}
              aria-label={t('migrationUI.source')}
              showSearch={false}
              autoComplete="off"
              value={source}
              disabled={busy}
              loading={sources.loading}
              placeholder={t('migrationUI.choose')}
              onChange={changeSource}
              options={(sources.value ?? [])
                .filter((item) =>
                  (item.display_name || item.name)
                    .toLowerCase()
                    .includes(sourceQuery.trim().toLowerCase()),
                )
                .map((item) => ({
                  value: item.project_id,
                  label: item.display_name || item.name,
                  disabled: !item.available || item.counts.total === 0,
                }))}
              optionRender={(option) => {
                const item = byId.get(String(option.value));
                return item ? (
                  <div className="vn-migration-source-option">
                    <Project project={item} />
                    <Typography.Text type="secondary">
                      {t('migrationUI.sourceCategories', {
                        manual: item.counts.manual ?? 0,
                        auto: item.counts.auto ?? 0,
                        quick: item.counts.quick ?? 0,
                        persistent: item.counts.persistent ?? 0,
                      })}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      {t('migrationUI.sourceCount', { count: item.counts.total })}
                      {!item.available && <> · {t('ui.unavailable')}</>}
                    </Typography.Text>
                  </div>
                ) : (
                  option.label
                );
              }}
              notFoundContent={t('migrationUI.noSources')}
            />
            {sources.loading && !sources.value && (
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            )}
            {!sources.loading && sources.value?.length === 0 && (
              <Empty description={t('migrationUI.noSources')} />
            )}
            {selected && (
              <div className="vn-migration-source-meta">
                <Project project={selected} />
                <div className="vn-migration-tags">
                  {['manual', 'auto', 'quick', 'persistent'].map((kind) => (
                    <Tag key={kind}>
                      {kindLabel(kind)} · {selected.counts[kind] ?? 0}
                    </Tag>
                  ))}
                </div>
                <Typography.Text>
                  {t('migrationUI.sourceCount', { count: selected.counts.total })}
                </Typography.Text>
              </div>
            )}
            <Checkbox
              checked={includePersistent}
              disabled={busy}
              onChange={(event) => {
                setIncludePersistent(event.target.checked);
                setPreview(null);
                setSkip(false);
              }}
            >
              {t('migrationUI.persistent')}
            </Checkbox>
            <Typography.Text type="secondary">{t('migrationUI.persistentNote')}</Typography.Text>
            <div className="vn-migration-actions">
              <Button
                aria-label={t('migrationUI.preview')}
                type="primary"
                icon={<ArrowRightLeft size={16} />}
                disabled={!selected?.available || selected.counts.total === 0 || busy}
                loading={operation === 'preview'}
                onClick={() => void loadPreview()}
              >
                {t('migrationUI.previewShort')}
              </Button>
            </div>
          </div>
        </Card>
        {preview && (
          <Card size="small">
            <div className="vn-content-page">
              <Typography.Title level={4}>{t('migrationUI.previewTitle')}</Typography.Title>
              <PreviewDetails key={preview.id} preview={preview} />
              {preview.counts.invalid > 0 && (
                <Checkbox
                  checked={skip}
                  disabled={busy}
                  onChange={(event) => setSkip(event.target.checked)}
                >
                  {t('migrationUI.skip')}
                </Checkbox>
              )}
              {preview.counts.valid === 0 && (
                <Alert type="warning" showIcon title={t('migration.noValid')} />
              )}
              <Typography.Text type="secondary">{t('migrationUI.commitNote')}</Typography.Text>
              <div className="vn-migration-actions">
                <Button
                  aria-label={t('migrationUI.start')}
                  type="primary"
                  loading={operation === 'submit'}
                  disabled={
                    busy || preview.counts.valid === 0 || (preview.counts.invalid > 0 && !skip)
                  }
                  onClick={() => void commit(preview, skip)}
                >
                  {t('migrationUI.startShort')}
                </Button>
              </div>
            </div>
          </Card>
        )}
      </>
    </>
  );
}

function MigrationHistory({
  history,
  retry,
  busy,
  openTask,
  previewUndo,
}: {
  history: History;
  retry: () => void;
  busy: boolean;
  openTask: (id: string) => void;
  previewUndo: (id: string) => void;
}) {
  return (
    <Card size="small" className="vn-migration-history">
      <div className="vn-content-page">
        <Typography.Title level={4}>{t('migrationUI.history')}</Typography.Title>
        {history.loading && !history.value && <Skeleton active />}
        {history.error && (
          <Alert
            type="error"
            showIcon
            title={t(history.error)}
            action={<Button onClick={retry}>{t('migrationUI.retry')}</Button>}
          />
        )}
        {history.value?.length === 0 && <Empty description={t('migrationUI.noHistory')} />}
        <div
          className="vn-group-list"
          aria-label={t('migrationUI.history')}
          aria-busy={history.loading}
        >
          {history.value?.map((record) => (
            <div key={record.task_id} className="vn-migration-record">
              <ContentRow
                icon={record.kind === 'undo' ? <Undo2 size={20} /> : <ArrowRightLeft size={20} />}
                title={t(record.kind === 'undo' ? 'migrationUI.undoTask' : 'migrationUI.task')}
                subtitle={
                  <span className="vn-migration-record-meta">
                    <span>{date(record.created_at)}</span>
                    <span className="vn-migration-tags">
                      <Tag>
                        {t(
                          phaseKeys.includes(record.state)
                            ? `migrationUI.phase.${record.state}`
                            : 'migrationUI.phase.recovering',
                        )}
                      </Tag>
                    </span>
                    <span>
                      {record.source.display_name || record.source.name} →{' '}
                      {record.target.display_name || record.target.name}
                    </span>
                    <span>
                      {t('migrationUI.taskCounts', {
                        completed: record.completed,
                        total: record.total,
                        skipped: record.skipped,
                      })}
                    </span>
                  </span>
                }
                onClick={() => openTask(record.task_id)}
              />
              {record.kind === 'migration' && record.state === 'completed' && !record.undo_id && (
                <div className="vn-migration-actions">
                  <Button
                    aria-label={t('migrationUI.undo')}
                    icon={<Undo2 size={16} />}
                    disabled={busy}
                    onClick={() => previewUndo(record.task_id)}
                  >
                    {t('migrationUI.undoShort')}
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function UndoConfirmation({
  undo,
  error,
  busy,
  onCancel,
  onConfirm,
}: {
  undo: Preview;
  error: MessageInput | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      open={true}
      title={t('migrationUI.undoTitle')}
      onCancel={() => {
        if (!busy) onCancel();
      }}
      footer={
        <div className="vn-migration-actions">
          <Button disabled={busy} onClick={onCancel}>
            {t('ui.cancel')}
          </Button>
          <Button
            aria-label={t('migrationUI.confirmUndo')}
            danger
            type="primary"
            loading={busy}
            disabled={busy}
            onClick={onConfirm}
          >
            {t('migrationUI.confirmShort')}
          </Button>
        </div>
      }
      className="vn-migration-modal"
      destroyOnHidden
    >
      <div className="vn-content-page">
        <Alert
          type="warning"
          showIcon
          title={t('migration.undoWarning')}
          description={t('migrationUI.undoNote')}
        />
        {error && <Alert type="error" showIcon title={t(error)} />}
        <Typography.Text>
          {t('migrationUI.changes', {
            add: undo.counts.add,
            overwrite: undo.counts.overwrite,
            remove: undo.entries.filter((entry) => entry.action === 'remove').length,
          })}
        </Typography.Text>
        <ul className="vn-migration-files" aria-label={t('migrationUI.files')}>
          {undo.entries.map((entry) => (
            <li key={entry.name}>
              <Typography.Text strong>{entry.name}</Typography.Text>
              <Tag>{actionLabel(entry.action)}</Tag>
              {!entry.valid && (
                <Alert type="warning" title={t(entry.reason ?? 'migrationUI.invalid')} />
              )}
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}
