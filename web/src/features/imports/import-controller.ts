import type { ImportSources, ArchiveUpload, ImportTask } from '../../api/types.js';
import { ApiError, requestData } from '../../api/client.js';
import { uploadBytes } from '../../api/binary-upload.js';
import {
  AppError,
  messageOf,
  type MessageInput,
} from '../../ui/preferences-store.js';
import { DirectoryUpload } from './directory-uploads.js';
import { Store } from '../../ui/store.js';
import type { Project } from '../../api/types.js';

export type SourceTab = 'upload' | 'folder' | 'server';
export type Files = ImportSources;
type Uploaded = ArchiveUpload;
export type Job = ImportTask;
type Snapshot = {
  tab: SourceTab;
  file: File | null;
  upload: Uploaded | null;
  source: { path: string; name: string; directory: boolean } | null;
  root?: number;
  path: string;
  listing: Files | null;
  browseLoading: boolean;
  browseError: MessageInput | null;
  job: Job | null;
  busy: boolean;
  cancelling: boolean;
  starting: boolean;
  restoring: boolean;
  pending: boolean;
  password: string;
  engineId: string;
  deleteSource: boolean;
  status: MessageInput | null;
  connection: MessageInput | null;
  uploadedBytes: number;
};
export const supported = /\.(zip|7z|rar|tar|tar\.gz|tar\.bz2|tar\.xz|apk)$/i;
const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class ImportController extends Store<Snapshot> {
  readonly directory: DirectoryUpload;
  private chunkRequest: AbortController | null = null;
  private browseRequest: AbortController | null = null;
  private transfer: Promise<void> | null = null;
  private timer: ReturnType<typeof setInterval> | undefined;
  private pendingBody: Record<string, unknown> | null = null;
  private requestId = '';
  private completedId = '';
  private generation = 0;
  private browseGeneration = 0;
  private directoryRestored = false;
  private started = false;
  private statusFlight: {
    controller: AbortController;
    done: Promise<void>;
    superseded: boolean;
  } | null = null;
  constructor(private done: (game: Project, warning?: MessageInput) => void) {
    super({
      tab: 'upload',
      file: null,
      upload: null,
      source: null,
      path: '',
      listing: null,
      browseLoading: false,
      browseError: null,
      job: null,
      busy: false,
      cancelling: false,
      starting: false,
      restoring: true,
      pending: false,
      password: '',
      engineId: '',
      deleteSource: false,
      status: null,
      connection: null,
      uploadedBytes: 0,
    });
    this.directory = new DirectoryUpload(() => this.publish());
    if (this.directory.active) this.publish({ tab: 'folder' });
  }
  setEngineId = (engineId: string) => this.publish({ engineId });
  private publish(patch: Partial<Snapshot> = {}) {
    this.update({ ...patch, pending: !!this.pendingBody });
  }
  async request<T>(path: string, init?: RequestInit): Promise<T> {
    return requestData<T>(path, init);
  }
  private post<T>(path: string, body: unknown) {
    return this.request<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }
  get active() {
    return !!this.data.job || !!this.data.upload || this.directory.active || this.data.busy;
  }
  get locked() {
    return this.active || this.data.cancelling || this.data.pending;
  }
  setPassword(value: string) {
    this.publish({ password: value });
  }
  setDeleteSource(value: boolean) {
    if (!this.locked) this.publish({ deleteSource: value });
  }
  chooseFile(file: File | null) {
    if (!this.locked) this.publish({ file, status: null });
  }
  chooseFolder(files: FileList | null) {
    try {
      this.directory.choose(files);
      this.publish({ status: null });
    } catch (error) {
      this.publish({ status: messageOf(error) });
    }
  }
  selectTab(tab: SourceTab) {
    if (this.locked && tab !== this.data.tab) return;
    this.publish({ tab });
    if (tab !== 'server') this.browseRequest?.abort();
    if (tab === 'server') void this.browse();
  }
  chooseRoot(root: number | undefined) {
    if (this.locked) return;
    this.publish({ root, path: '', source: null });
    void this.browse();
  }
  browsePath(path: string) {
    if (!this.locked) {
      this.publish({ path, source: null });
      void this.browse();
    }
  }
  selectCurrent() {
    const listing = this.data.listing;
    if (
      !this.locked &&
      !this.data.browseLoading &&
      !this.data.browseError &&
      listing?.project &&
      listing.source
    )
      this.publish({ source: { path: listing.source, name: listing.source, directory: true } });
  }
  selectEntry(entry: Files['entries'][number]) {
    const listing = this.data.listing;
    if (
      this.locked ||
      this.data.browseLoading ||
      this.data.browseError ||
      !listing ||
      !listing.entries.some(
        (item) => item.path === entry.path && item.directory === entry.directory,
      ) ||
      (!entry.directory && (!supported.test(entry.name) || !listing.source))
    )
      return;
    if (entry.directory) this.browsePath(entry.path);
    else
      this.publish({
        deleteSource: false,
        source: {
          path: `${this.data.listing!.source}/${entry.name}`,
          name: entry.name,
          directory: false,
        },
      });
  }
  private async browse() {
    this.browseRequest?.abort();
    const controller = new AbortController();
    this.browseRequest = controller;
    const stamp = ++this.browseGeneration;
    // Keep the root picker available while invalidating content from its old location.
    this.publish({
      browseLoading: true,
      browseError: null,
      listing: this.data.listing ? { roots: this.data.listing.roots, entries: [] } : null,
    });
    try {
      const { root, path } = this.data;
      const listing = await this.request<Files>(
        root === undefined
          ? 'imports/sources'
          : `imports/sources?root=${root}&path=${encodeURIComponent(path)}`,
        { signal: controller.signal },
      );
      if (stamp === this.browseGeneration && this.data.tab === 'server')
        this.publish({ listing, browseLoading: false });
    } catch (error) {
      if (!controller.signal.aborted && stamp === this.browseGeneration)
        this.publish({ browseError: messageOf(error), browseLoading: false });
    } finally {
      if (this.browseRequest === controller) this.browseRequest = null;
    }
  }
  private accept(job: Job) {
    this.directory.detach();
    this.pendingBody = null;
    this.publish({
      job,
      engineId: job.engine_id ?? '',
      tab: job.directory_upload_id ? 'folder' : job.upload_id ? 'upload' : 'server',
      starting: false,
      busy: false,
      connection: null,
      ...(job.state !== 'failed' ? { password: '' } : {}),
    });
    if (job.state === 'completed' && job.project && this.completedId !== job.task_id) {
      this.completedId = job.task_id;
      const warning = job.messages?.warning ?? job.warning;
      this.requestId = '';
      this.generation++;
      this.publish({
        job: null,
        file: null,
        upload: null,
        source: null,
        password: '',
        status: null,
        uploadedBytes: 0,
      });
      this.done(job.project, warning ? messageOf(warning) : undefined);
    }
  }
  poll = async (foreground = false) => {
    if (this.data.starting || this.data.busy || (this.data.upload && !this.data.job)) return;
    const previous = this.statusFlight;
    if (previous) {
      if (!foreground) return;
      previous.superseded = true;
      previous.controller.abort();
      await previous.done;
    }
    if (
      this.statusFlight ||
      this.data.starting ||
      this.data.busy ||
      (this.data.upload && !this.data.job)
    )
      return;
    let finish!: () => void;
    const flight = {
      controller: new AbortController(),
      done: new Promise<void>((resolve) => {
        finish = resolve;
      }),
      superseded: false,
    };
    this.statusFlight = flight;
    const stamp = this.generation;
    const deadline = setTimeout(() => flight.controller.abort(), 5000);
    try {
      const result = await this.request<Job | null>(
        this.data.job ? `tasks/imports/${this.data.job.task_id}` : 'imports/active',
        { signal: flight.controller.signal },
      );
      if (this.statusFlight !== flight || stamp !== this.generation || flight.superseded) return;
      this.publish({ restoring: false });
      if (result && !result.abandoned) this.accept(result);
      if (!this.directoryRestored && !result) {
        this.publish({ restoring: true });
        await this.directory.restore();
        if (stamp !== this.generation || flight.superseded) return;
        this.directoryRestored = true;
        this.publish({
          restoring: false,
          ...(this.directory.active ? { tab: 'folder' as const } : {}),
        });
      }
      this.publish({ connection: null });
    } catch {
      if (this.statusFlight === flight && stamp === this.generation && !flight.superseded)
        this.publish({ connection: 'ui.connectionInterruptedRetrying' });
    } finally {
      clearTimeout(deadline);
      if (this.statusFlight === flight) this.statusFlight = null;
      finish();
    }
  };
  private async chunk(upload: Uploaded, file: File): Promise<Uploaded> {
    const offset = upload.offset_bytes;
    const controller = new AbortController();
    this.chunkRequest = controller;
    try {
      const next = await uploadBytes<Uploaded>(
        `uploads/archives/${upload.upload_id}?offset_bytes=${offset}`,
        file.slice(offset, offset + 8 * 1024 * 1024),
        {
          signal: controller.signal,
          progress: (event) => this.publish({ uploadedBytes: offset + event.loaded }),
        },
      );
      if (!next || next.upload_id !== upload.upload_id || !Number.isSafeInteger(next.offset_bytes))
        throw new AppError('imports.invalidResponse');
      return next;
    } finally {
      this.chunkRequest = null;
    }
  }
  startImport = () => {
    if (this.transfer || this.data.busy || this.data.cancelling || this.data.job?.state === 'running')
      return;
    this.transfer = this.runImport().finally(() => {
      this.transfer = null;
    });
    return this.transfer;
  };
  private async runImport() {
    this.generation++;
    this.publish({ busy: true, status: null, connection: null });
    const { job, tab, file } = this.data;
    const password = (job ? !job.directory_upload_id : tab !== 'folder')
      ? this.data.password || undefined
      : undefined;
    try {
      if (job?.state === 'failed') {
        this.publish({ starting: true });
        this.accept(
          await this.post<Job>(`tasks/imports/${job.task_id}/retry`, {
            password,
            ...(this.data.engineId !== (job.engine_id ?? '')
              ? { engine_id: this.data.engineId || null }
              : {}),
          }),
        );
        return;
      }
      if (tab === 'upload' && !this.pendingBody) {
        if (!file || !supported.test(file.name))
          throw new AppError('ui.chooseASupportedFileFormat');
        let upload =
          this.data.upload ??
          (await this.post<Uploaded>('uploads/archives', {
            filename: file.name,
            size_bytes: file.size,
          }));
        this.publish({ upload, uploadedBytes: upload.offset_bytes });
        upload = await this.request<Uploaded>(`uploads/archives/${upload.upload_id}`);
        this.publish({ upload, uploadedBytes: upload.offset_bytes });
        let failures = 0;
        while (upload.offset_bytes < file.size) {
          if (this.data.cancelling) return;
          const before = upload.offset_bytes;
          try {
            upload = await this.chunk(upload, file);
            if (upload.offset_bytes <= before || upload.offset_bytes > file.size)
              throw new AppError('imports.invalidOffset');
            failures = 0;
          } catch (error) {
            if (this.data.cancelling) return;
            if (++failures > 3) throw error;
            this.publish({ connection: 'ui.connectionInterruptedRetrying' });
            await wait(500);
            if (this.data.cancelling) return;
            upload = await this.request<Uploaded>(`uploads/archives/${upload.upload_id}`);
          }
          this.publish({ upload, uploadedBytes: upload.offset_bytes });
        }
      }
      let directoryId: string | undefined;
      if (tab === 'folder' && !this.pendingBody) {
        directoryId = await this.directory.upload();
        this.requestId = this.directory.requestId;
      }
      if (this.data.cancelling) return;
      this.requestId ||= crypto.randomUUID();
      this.pendingBody ||= {
        request_id: this.requestId,
        ...(this.data.engineId ? { engine_id: this.data.engineId } : {}),
        ...(tab === 'folder'
          ? { directory_upload_id: directoryId }
          : tab === 'upload'
            ? { upload_id: this.data.upload!.upload_id }
            : {
                path: this.data.source!.path,
                delete_source: !this.data.source!.directory && this.data.deleteSource,
              }),
      };
      this.publish({ starting: true });
      this.accept(
        await this.post<Job>('imports', {
          ...this.pendingBody,
          password,
        }),
      );
      this.publish({ password: '' });
    } catch (error) {
      if (!this.data.cancelling) this.publish({ status: messageOf(error, 'common.networkFailed') });
    } finally {
      this.publish({ busy: false, starting: false });
    }
  }
  cancel = async () => {
    if (this.data.cancelling || this.data.starting || this.data.job) return;
    this.publish({ cancelling: true });
    this.chunkRequest?.abort();
    try {
      if (this.data.tab === 'folder' || this.directory.active) await this.directory.cancel();
      await this.transfer;
      const upload = this.data.upload;
      if (upload)
        for (let attempt = 0; ; attempt++) {
          try {
            await this.request(`uploads/archives/${upload.upload_id}`, { method: 'DELETE' });
            break;
          } catch (error) {
            if (error instanceof ApiError && error.status === 404) break;
            if (!(error instanceof ApiError) || error.status !== 409 || attempt >= 12)
              throw error;
            await wait(250);
          }
        }
      this.pendingBody = null;
      this.requestId = '';
      this.publish({
        upload: null,
        file: null,
        busy: false,
        uploadedBytes: 0,
        status: 'ui.uploadCancelled',
      });
    } catch (error) {
      this.publish({ status: messageOf(error) });
    } finally {
      this.publish({ cancelling: false });
    }
  };
  abandon = async () => {
    if (this.data.job?.state !== 'failed' || this.data.busy) return;
    this.publish({ busy: true });
    try {
      await this.request(`tasks/imports/${this.data.job.task_id}`, { method: 'DELETE' });
      this.pendingBody = null;
      this.requestId = '';
      this.generation++;
      this.publish({
        job: null,
        upload: null,
        file: null,
        source: null,
        password: '',
        status: null,
      });
    } catch (error) {
      this.publish({ status: messageOf(error) });
    } finally {
      this.publish({ busy: false });
    }
  };
  private onHashChange = () => {
    if (location.hash === '#add') void this.poll();
  };
  private onVisibilityChange = () => {
    if (!document.hidden) void this.poll(true);
  };
  start = () => {
    if (this.started) return;
    this.started = true;
    window.addEventListener('hashchange', this.onHashChange);
    document.addEventListener('visibilitychange', this.onVisibilityChange);
    this.timer = setInterval(() => {
      if (!document.hidden && (!this.data.job || this.data.job.state === 'running'))
        void this.poll();
    }, 1000);
    void this.poll();
  };
  stop = () => {
    if (!this.started) return;
    this.started = false;
    clearInterval(this.timer);
    window.removeEventListener('hashchange', this.onHashChange);
    document.removeEventListener('visibilitychange', this.onVisibilityChange);
    this.browseRequest?.abort();
    if (this.statusFlight) {
      this.statusFlight.superseded = true;
      this.statusFlight.controller.abort();
    }
  };
}
