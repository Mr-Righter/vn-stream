import type { DirectoryFile, DirectoryUploadRecord } from '../../api/types.js';
import { ApiError, requestData } from '../../api/client.js';
import { uploadBytes } from '../../api/binary-upload.js';
import { AppError } from '../../ui/preferences-store.js';

const CHUNK = 8 * 1024 * 1024;
const BATCH = 1000;
const STORAGE = 'vn-stream:directory-upload';
type Entry = DirectoryFile;
type Snapshot = DirectoryUploadRecord;
type Selection = { name: string; total: number; files: { path: string; file: File }[] };
const cancelled = () => new DOMException('Cancelled', 'AbortError');
const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Owns directory metadata and byte transfer; the existing import controller owns jobs. */
export class DirectoryUpload {
  readonly supported = 'webkitdirectory' in document.createElement('input');
  snapshot: Snapshot | null = null;
  selection: Selection | null = null;
  requestId = '';
  sentBytes = 0;
  storageAvailable = true;
  private savedId = '';
  private cancelled = false;
  private chunkRequest: AbortController | null = null;
  private running: Promise<string> | null = null;

  constructor(private changed: () => void) {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE) || 'null');
      if (value && typeof value.id === 'string' && /^[a-f0-9]{32}$/.test(value.id)) {
        this.savedId = value.id;
        this.requestId = typeof value.requestId === 'string' ? value.requestId : '';
      }
    } catch {
      this.storageAvailable = false;
    }
  }
  get active() {
    return !!this.savedId;
  }
  get ready() {
    return !!this.selection || this.snapshot?.state === 'complete';
  }
  get name() {
    return this.snapshot?.name ?? this.selection?.name;
  }
  get count() {
    return this.snapshot?.file_count ?? this.selection?.files.length ?? 0;
  }
  get total() {
    return this.snapshot?.total_bytes ?? this.selection?.total ?? 0;
  }
  get confirmed() {
    return this.snapshot?.uploaded_bytes ?? 0;
  }
  get needsSelection() {
    return this.active && !this.selection && this.snapshot?.state !== 'complete';
  }

  private persist() {
    try {
      if (this.savedId)
        localStorage.setItem(
          STORAGE,
          JSON.stringify({ id: this.savedId, requestId: this.requestId }),
        );
      else localStorage.removeItem(STORAGE);
    } catch {
      this.storageAvailable = false;
    }
  }
  detach() {
    this.savedId = '';
    this.requestId = '';
    this.snapshot = null;
    this.selection = null;
    this.sentBytes = 0;
    this.persist();
  }
  private async request<R>(suffix = '', method = 'GET', body?: unknown): Promise<R> {
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 5000);
    try {
      return await requestData<R>(`uploads/directories${suffix}`, {
        method,
        signal: controller.signal,
        ...(body === undefined ? {} : {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
      });
    } catch (error) {
      if (controller.signal.aborted && !this.cancelled) throw new AppError('common.requestTimeout');
      throw error;
    } finally {
      clearTimeout(deadline);
    }
  }
  private async refresh(first?: Snapshot) {
    first ??= await this.request<Snapshot>(`/${this.savedId}?cursor=0&limit=${BATCH}`);
    const files = [...first.files];
    let cursor = first.next_cursor;
    while (cursor !== null) {
      const next = await this.request<Snapshot>(`/${this.savedId}?cursor=${cursor}&limit=${BATCH}`);
      files.push(...next.files);
      cursor = next.next_cursor;
    }
    this.snapshot = { ...first, files, next_cursor: null };
    this.sentBytes = first.uploaded_bytes;
    this.changed();
  }
  private async discover() {
    let found: Snapshot | null;
    try {
      found = await this.request<Snapshot | null>('/active');
    } catch (error) {
      // Cleanup may remove the row between active-ID discovery and status lookup.
      if (error instanceof ApiError && error.status === 404) return false;
      throw error;
    }
    if (!found) return false;
    if (this.savedId !== found.upload_id) this.requestId = '';
    this.savedId = found.upload_id;
    this.requestId ||= crypto.randomUUID();
    this.persist();
    await this.refresh(found);
    return true;
  }
  async restore() {
    if (!(await this.discover())) {
      this.detach();
      this.changed();
    }
  }
  choose(files: FileList | null) {
    this.selection = null;
    if (!files?.length) throw new AppError('ui.thereAreNoFilesToUploadEmptyFoldersCannot');
    if (files.length > 100000) throw new AppError('ui.theFolderContainsTooManyFiles');
    let name = '',
      total = 0;
    const paths = new Set<string>();
    const selected: Selection['files'] = [];
    for (const file of Array.from(files)) {
      const parts = file.webkitRelativePath.split('/');
      const root = parts.shift()!;
      if (!name) name = root;
      if (
        !root ||
        root !== name ||
        !parts.length ||
        parts.length > 33 ||
        [root, ...parts].some(
          (part) =>
            !part ||
            part === '.' ||
            part === '..' ||
            part === '.vn-stream' ||
            /[\\:\u0000-\u001f\u007f]/.test(part),
        )
      ) {
        throw new AppError('ui.chooseOneCompleteGameFolder');
      }
      const path = parts.join('/');
      if (paths.has(path)) throw new AppError('ui.chooseOneCompleteGameFolder');
      paths.add(path);
      total += file.size;
      selected.push({ path, file });
    }
    selected.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
    const selection = { name, total, files: selected };
    this.validate(selection);
    this.selection = selection;
    this.changed();
  }
  private validate(selection: Selection) {
    const state = this.snapshot;
    if (!state) return;
    if (
      selection.name !== state.name ||
      selection.total !== state.total_bytes ||
      selection.files.length !== state.file_count ||
      state.files.some(
        (entry, i) =>
          selection.files[i]?.path !== entry.path ||
          selection.files[i]?.file.size !== entry.size_bytes,
      ) ||
      (state.state !== 'manifest' && state.files.length !== selection.files.length)
    ) {
      throw new AppError('ui.theFolderDoesNotMatchTheSavedManifestReselect');
    }
  }
  private checkCancelled() {
    if (this.cancelled) throw cancelled();
  }
  private async chunk(entry: Entry, file: File): Promise<Entry> {
    this.checkCancelled();
    const controller = new AbortController();
    this.chunkRequest = controller;
    try {
      return await uploadBytes<Entry>(
        `uploads/directories/${this.savedId}/files/${entry.file_id}?offset_bytes=${entry.offset_bytes}`,
        file.slice(entry.offset_bytes, entry.offset_bytes + CHUNK),
        { signal: controller.signal, progress: (event) => {
          this.sentBytes = this.confirmed + event.loaded;
          this.changed();
        } },
      );
    } finally {
      this.chunkRequest = null;
    }
  }
  upload(): Promise<string> {
    if (this.running) return this.running;
    this.cancelled = false;
    this.running = this.transfer().finally(() => {
      this.running = null;
      this.chunkRequest = null;
    });
    return this.running;
  }
  private async transfer() {
    if (!this.snapshot) {
      if (!this.selection) throw new AppError('ui.chooseOneCompleteGameFolder');
      try {
        const result = await this.request<Snapshot>('', 'POST', {
          name: this.selection.name,
          file_count: this.count,
          total_bytes: this.total,
        });
        this.savedId = result.upload_id;
        this.snapshot = result;
        this.requestId ||= crypto.randomUUID();
        this.persist();
        this.changed();
      } catch (error) {
        // A create can commit before its response is lost. Discover and validate
        // that transfer instead of sending a second create or abandoning its ID.
        if (error instanceof ApiError && error.status < 500 && error.status !== 409) throw error;
        if (!(await this.discover())) throw error;
        try {
          this.validate(this.selection);
        } catch (mismatch) {
          this.selection = null;
          throw mismatch;
        }
      }
    }
    this.checkCancelled();
    await this.refresh();
    this.checkCancelled();
    if (this.snapshot!.state === 'complete') return this.savedId;
    if (!this.selection) throw new AppError('ui.reselectTheSameOriginalFolderToContinueUploading');
    this.validate(this.selection);
    if (this.snapshot!.state === 'manifest') {
      for (let i = 0; i < this.selection.files.length; i += BATCH) {
        this.checkCancelled();
        await this.request(`/${this.savedId}/manifest`, 'POST', {
          batch_id: String(i / BATCH),
          files: this.selection.files
            .slice(i, i + BATCH)
            .map(({ path, file }) => ({ path, size_bytes: file.size })),
        });
      }
      this.checkCancelled();
      await this.request(`/${this.savedId}/seal`, 'POST');
      await this.refresh();
    }
    for (let index = 0; index < this.snapshot!.files.length; index++) {
      let failures = 0;
      while (true) {
        this.checkCancelled();
        const entry = this.snapshot!.files[index];
        if (entry.offset_bytes === entry.size_bytes) break;
        const file = this.selection.files[index].file;
        try {
          const next = await this.chunk(entry, file);
          if (
            next.file_id !== entry.file_id ||
            next.path !== entry.path ||
            next.size_bytes !== entry.size_bytes ||
            next.offset_bytes !== Math.min(entry.size_bytes, entry.offset_bytes + CHUNK)
          )
            throw new AppError('ui.invalidUploadResponse');
          this.snapshot!.uploaded_bytes += next.offset_bytes - entry.offset_bytes;
          this.snapshot!.files[index] = next;
          failures = 0;
        } catch (error) {
          this.checkCancelled();
          this.sentBytes = this.confirmed;
          this.changed();
          if (++failures > 3) throw error;
          await wait(400);
          this.checkCancelled();
          await this.refresh();
          this.validate(this.selection);
        }
        this.sentBytes = this.confirmed;
        this.changed();
      }
    }
    this.checkCancelled();
    await this.request(`/${this.savedId}/complete`, 'POST');
    await this.refresh();
    this.checkCancelled();
    return this.savedId;
  }
  async cancel() {
    this.cancelled = true;
    this.chunkRequest?.abort();
    await this.running?.catch(() => {});
    if (this.savedId) {
      for (let attempt = 0; ; attempt++) {
        try {
          await this.request(`/${this.savedId}`, 'DELETE');
          break;
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) break;
          if (!(error instanceof ApiError) || error.status !== 409 || attempt >= 12) throw error;
          await wait(250);
        }
      }
    }
    this.detach();
    this.changed();
  }
}
