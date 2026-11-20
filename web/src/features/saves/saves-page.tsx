import { Alert, Button } from 'antd';
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef } from 'react';
import { contentAPI, useContent } from '../../api/content-client.js';
import type { MigrationTask, SaveListing, SigningState } from '../../api/types.js';
import { contentHash, type ContentRoute } from '../../app/content-route.js';
import { t } from '../../ui/preferences-store.js';
import { SaveDetail } from './save-detail.js';
import { SaveList } from './save-list.js';
import { SaveMigration } from './save-migration.js';
import { useSaveActions } from './save-actions.js';

const signingInProgress = (state: string) => ['queued', 'waiting', 'running'].includes(state);
const migrationInProgress = (state: string) => ['queued', 'running', 'recovering'].includes(state);

type Props = {
  projectId: string;
  route: ContentRoute;
  canEdit: boolean;
  canMigrate: boolean;
};

export function SavesPage(props: Props) {
  return <ProjectSaves key={props.projectId} {...props} />;
}

function ProjectSaves({ projectId, route, canEdit, canMigrate }: Props) {
  const prefix = `projects/${encodeURIComponent(projectId)}/saves`;
  const active = route.tab === 'saves';
  const migrating = active && canMigrate && route.view === 'migrate';
  const managing = active && !migrating;
  const uid = route.view === 'save' ? route.uid : '';
  const [refresh, reload] = useReducer((value) => value + 1, 0);
  const [historyRevision, reloadHistory] = useReducer((value) => value + 1, 0);
  const listing = useContent<SaveListing>(prefix, refresh, { enabled: managing });
  const signing = useContent<SigningState>(`${prefix}/signing`, refresh, {
    enabled: canEdit && managing,
  });
  const history = useContent<MigrationTask[]>(
    canMigrate ? `${prefix}/migrations` : null,
    historyRevision,
    { enabled: active },
  );
  const previousView = useRef({ active, migrating });
  const taskFlight = useRef<Promise<void> | null>(null);
  const migrationChanged = useCallback(() => {
    reload();
    reloadHistory();
  }, []);

  useLayoutEffect(() => {
    const previous = previousView.current;
    previousView.current = { active, migrating };
    if (canMigrate && active && previous.active && previous.migrating !== migrating)
      reloadHistory();
  }, [active, migrating, canMigrate]);

  const signingState = signing.value?.state ?? '';
  useEffect(() => {
    if (!managing || !canEdit || signing.loading || !signingInProgress(signingState)) return;
    const timer = setTimeout(reload, 1500);
    return () => clearTimeout(timer);
  }, [managing, canEdit, signing.loading, signingState, refresh]);

  const { value: batches, loading: historyLoading, error: historyError } = history;
  useEffect(() => {
    if (!canMigrate || !managing || historyLoading) return;
    if (historyError) {
      const timer = setTimeout(reloadHistory, 1500);
      return () => clearTimeout(timer);
    }
    if (!batches?.length) return;
    reload();
    if (batches.some((task) => migrationInProgress(task.state))) {
      const timer = setTimeout(reloadHistory, 1200);
      return () => clearTimeout(timer);
    }
  }, [canMigrate, managing, batches, historyLoading, historyError]);

  if (!active) return null;

  if (migrating) {
    return (
      <SaveMigration
        projectId={projectId}
        prefix={prefix}
        params={route.params}
        history={history}
        reloadHistory={reloadHistory}
        taskFlight={taskFlight}
        onChanged={migrationChanged}
      />
    );
  }

  const isSigning = canEdit && signingInProgress(signingState);
  return (
    <section aria-label={t('saves.title')} className="vn-content-page">
      {listing.error && <Alert type="error" showIcon title={t(listing.error)} />}
      {canEdit && signing.value && (isSigning || signing.value.failed > 0) && (
        <SaveSigning prefix={prefix} state={signing.value} onChanged={reload} />
      )}
      {listing.value && !listing.value.writable && !isSigning && (
        <Alert type="info" showIcon title={t('saves.stopFirst')} />
      )}
      {uid ? (
        <SaveDetail
          key={uid}
          uid={uid}
          prefix={prefix}
          listHash={contentHash(projectId, 'saves', '', '', route.params)}
          listing={listing.value}
          canEdit={canEdit}
          refresh={refresh}
          onChanged={reload}
        />
      ) : (
        <SaveList
          prefix={prefix}
          projectId={projectId}
          params={route.params}
          listing={listing.value}
          loading={listing.loading}
          canMigrate={canMigrate}
          onChanged={reload}
        />
      )}
    </section>
  );
}

function SaveSigning({
  prefix,
  state,
  onChanged,
}: {
  prefix: string;
  state: SigningState;
  onChanged: () => void;
}) {
  const { run, busy, error } = useSaveActions(onChanged);
  return (
    <>
      {error && <Alert type="error" showIcon title={t(error)} />}
      <Alert
        type={state.failed > 0 ? 'warning' : 'info'}
        showIcon
        title={t(
          ['queued', 'waiting'].includes(state.state) ? 'saves.signingWaiting' : 'saves.signing',
        )}
        description={
          <div>
            <div>
              {t('saves.signingSummary', {
                signed: state.signed,
                skipped: state.skipped,
                failed: state.failed,
              })}
            </div>
            {(state.errors ?? []).map((error, i) => (
              <div key={i}>
                {error.name}: {t(error.code)}
              </div>
            ))}
            {state.state === 'completed' && state.failed > 0 && (
              <Button
                loading={busy}
                onClick={() => void run(() => contentAPI(`${prefix}/signing/retry`, {}))}
              >
                {t('saves.signingRetry')}
              </Button>
            )}
          </div>
        }
      />
    </>
  );
}
