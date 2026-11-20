import {
  deleteProject,
  getProjectDeletion,
  getStream,
  listEngines,
  listProjects,
  refreshProjects,
  retryProjectDeletion,
  startProject,
} from '../../api/library.js';
import { getSession, stopSession, type Session } from '../../api/player.js';
import type { Engine, Project, ProjectDeletion } from '../../api/types.js';
import { contentHash, navigateContent } from '../../app/content-route.js';
import { AppError, message, messageOf, type MessageInput } from '../../ui/preferences-store.js';
import { Store } from '../../ui/store.js';

type Snapshot = {
  games: Project[];
  engines: Engine[];
  session: Session;
  loaded: boolean;
  failed: boolean;
  starting: string | null;
  choices: Record<string, string>;
  notice: MessageInput | null;
  scanTruncated: boolean;
};
export class LibraryController extends Store<Snapshot> {
  private refreshing: Promise<void> | null = null;
  private checking: Promise<void> | null = null;
  private revision = 0;
  private collectionRevision = 0;
  private pollTimer?: ReturnType<typeof setInterval>;
  private noticeTimer?: ReturnType<typeof setTimeout>;
  private deletions = new Set<string>();
  private scanSummary: MessageInput | null = null;
  constructor() {
    super({
      games: [],
      engines: [],
      session: { state: 'loading' },
      loaded: false,
      failed: false,
      starting: null,
      choices: {},
      notice: null,
      scanTruncated: false,
    });
  }
  private async loadProjects(rescan: boolean) {
    if (!rescan) return listProjects();
    const { data, headers } = await refreshProjects();
    this.scanSummary = message('library.refreshSummary', {
      added: Number(headers.get('X-Discovery-Added') ?? 0),
      warnings: Number(headers.get('X-Discovery-Warnings') ?? 0),
    });
    this.update({ scanTruncated: headers.get('X-Discovery-Truncated') === 'true' });
    return data;
  }
  notify = (notice: MessageInput) => {
    clearTimeout(this.noticeTimer);
    this.update({ notice: messageOf(notice) });
    this.noticeTimer = setTimeout(() => this.update({ notice: null }), 4000);
  };
  action = async (work: () => Promise<unknown>) => {
    try {
      await work();
    } catch (error) {
      this.notify(messageOf(error, 'common.networkFailed'));
    }
  };
  choice(game: Project) {
    const value = this.data.choices[game.project_id] ?? game.runtime_override ?? '';
    return value === game.recommendation.runtime_id ? '' : value;
  }
  choose(game: Project, value: string) {
    this.update({ choices: { ...this.data.choices, [game.project_id]: value } });
  }
  private async refreshSession() {
    if (this.checking) return this.checking;
    const revision = this.revision;
    this.checking = getSession()
      .then((session) => {
        if (revision === this.revision && !this.data.starting) this.update({ session });
      })
      .finally(() => {
        this.checking = null;
      });
    return this.checking;
  }
  refresh = async (rescan = false, afterPending = false): Promise<void> => {
    if (this.refreshing) {
      if (!rescan && !afterPending) {
        await Promise.all([this.refreshing, this.refreshSession()]);
        return;
      }
      await this.refreshing.catch(() => {});
      return this.refresh(rescan, afterPending);
    }
    const collectionRevision = this.collectionRevision;
    const request = (async () => {
      const results = await Promise.allSettled([
        this.loadProjects(rescan)
          .then((games) => {
            if (collectionRevision !== this.collectionRevision) return;
            if (!Array.isArray(games)) throw new AppError('common.requestFailed');
            this.update({ games, loaded: true, failed: false });
            for (const game of games)
              if (game.deletion?.state === 'running')
                void this.action(() =>
                  this.followDeletion(game.project_id, game.deletion!.task_id),
                );
          })
          .catch((error) => {
            this.update({ failed: true });
            throw error;
          }),
        listEngines().then((engines) => {
          if (!Array.isArray(engines)) throw new AppError('common.requestFailed');
          this.update({ engines });
        }),
        this.refreshSession(),
      ]);
      const failed = results.find((result) => result.status === 'rejected');
      if (failed?.status === 'rejected') throw failed.reason;
    })();
    this.refreshing = request;
    try {
      await request;
    } finally {
      this.refreshing = null;
    }
  };
  rescan = async () => {
    await this.refresh(true);
    this.notify(this.scanSummary ?? 'ui.libraryRefreshed');
  };
  play = async (game: Project) => {
    if (this.data.starting) return;
    this.revision++;
    this.update({ starting: game.project_id });
    try {
      let session = await getSession();
      if (session.state !== 'running')
        session = await startProject(game.project_id, this.choice(game) || null);
      this.update({ session });
      if (session.project_id !== game.project_id)
        throw new AppError('ui.anotherGameIsRunningSaveAndStopItFirst');
      const stream = await getStream();
      if (!stream.available || !stream.url)
        throw new AppError('ui.streamingIsNotEnabledOnThisService');
      const url = new URL(stream.url, location.origin);
      if (session.session_id) url.searchParams.set('session', session.session_id);
      location.assign(url.href);
    } finally {
      this.revision++;
      this.update({ starting: null });
    }
  };
  stop = async () => {
    await stopSession();
    await this.refresh();
  };
  remove = async (game: Project) => {
    const job = await deleteProject(game.project_id);
    this.updateDeletion(game.project_id, job);
    void this.action(() => this.followDeletion(game.project_id, job.task_id));
  };
  retryDeletion = (game: Project) =>
    this.followDeletion(game.project_id, game.deletion!.task_id, true);
  private updateDeletion(projectId: string, deletion: ProjectDeletion) {
    this.collectionRevision++;
    this.update({
      games: this.data.games.map((game) =>
        game.project_id === projectId ? { ...game, deletion } : game,
      ),
    });
  }
  private async followDeletion(projectId: string, id: string, retry = false) {
    if (this.deletions.has(id)) return;
    this.deletions.add(id);
    try {
      if (retry) this.updateDeletion(projectId, await retryProjectDeletion(id));
      while (true) {
        const job = await getProjectDeletion(id);
        if (job.state === 'completed') {
          this.collectionRevision++;
          this.update({
            games: this.data.games.filter((item) => item.project_id !== projectId),
          });
          navigateContent('');
          this.notify('ui.gameDeleted');
          await this.refresh(false, true);
          return;
        }
        if (job.state === 'failed') {
          this.updateDeletion(projectId, job);
          throw new AppError(messageOf(job.messages?.error ?? job.error));
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    } finally {
      this.deletions.delete(id);
    }
  }
  added = (game: Project, warning?: MessageInput) => {
    this.collectionRevision++;
    this.update({
      games: [...this.data.games.filter((item) => item.project_id !== game.project_id), game],
      loaded: true,
    });
    navigateContent(contentHash(game.project_id, 'info'));
    this.notify(warning ?? 'ui.gameAdded');
    void this.refresh(false, true).catch(() => {
      this.update({
        games: [...this.data.games.filter((item) => item.project_id !== game.project_id), game],
      });
      this.notify('library.addedRefreshFailed');
    });
  };
  private pollIfVisible = () => {
    if (!document.hidden) void this.action(() => this.refresh());
  };
  start() {
    if (this.pollTimer !== undefined) return;
    try {
      const returned = sessionStorage.getItem('vn-stream:return');
      if (returned) {
        try {
          this.notify(JSON.parse(returned));
        } catch {
          this.notify(returned);
        }
        sessionStorage.removeItem('vn-stream:return');
      }
    } catch {
      // Storage restrictions must not prevent loading the library or session.
    }
    document.addEventListener('visibilitychange', this.pollIfVisible);
    this.pollTimer = setInterval(this.pollIfVisible, 5000);
    void this.action(() => this.refresh());
  }
  stopPolling() {
    clearInterval(this.pollTimer);
    this.pollTimer = undefined;
    document.removeEventListener('visibilitychange', this.pollIfVisible);
  }
}
