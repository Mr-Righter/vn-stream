import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect, setAppearance, type Page } from './fixtures.js';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { basename, join, resolve } from 'node:path';
// Self-authored data only. No engine or game is launched by these tests.
let fixtures: string[] = [];
let uploadIds: string[] = [];
async function folder(
  files: Record<string, string | Buffer> = { 'game/script.rpy': 'label start:\n    return\n' },
) {
  const root = await mkdtemp(resolve('../var/e2e', 'Native-'));
  fixtures.push(root);
  for (const [path, content] of Object.entries(files)) {
    await mkdir(join(root, path, '..'), { recursive: true });
    await writeFile(join(root, path), content);
  }
  return root;
}
async function openFolder(page: Page) {
  await page.goto('/#add');
  await page.getByRole('radio', { name: '上传文件夹', exact: true }).locator('..').click();
  await expect(page.locator('#folder-file')).toHaveAttribute('webkitdirectory', '');
}
async function choose(page: Page, root: string) {
  await page.locator('#folder-file').setInputFiles(root);
  const paths = await page
    .locator('#folder-file')
    .evaluate((n: HTMLInputElement) => [...n.files!].map((f) => f.webkitRelativePath));
  expect(paths.length).toBeGreaterThan(0);
  expect(paths.every((p) => p.startsWith(`${basename(root)}/`))).toBe(true);
}
test.beforeEach(async ({ page }) => {
  fixtures = [];
  uploadIds = [];
  await page.route('**/api/v2/uploads/directories', async (route) => {
    const response = await route.fetch();
    if (response.status() === 201) uploadIds.push(apiData(await response.json()).upload_id);
    await route.fulfill({ response });
  });
});
test.afterEach(async ({ page, request }) => {
  await page.unrouteAll({ behavior: 'wait' });
  const active = apiData(await (await request.get('/api/v2/imports/active')).json());
  if (active?.state === 'failed') await request.delete(`/api/v2/tasks/imports/${active.task_id}`);
  for (const id of uploadIds) await request.delete(`/api/v2/uploads/directories/${id}`);
  for (const path of fixtures) await rm(path, { recursive: true, force: true });
});
test('native directory picker uploads complete relative tree in bounded sequential chunks', async ({
  page,
}) => {
  const blob = Buffer.alloc(8 * 1024 * 1024 + 7, 19);
  const root = await folder({
    'game/script.rpy': 'label start:\n    return\n',
    'game/saves/persistent': 'saved-progress',
    'game/assets/data.bin': blob,
    '.hidden': 'hidden',
    'empty.dat': '',
  });
  const chunks: number[] = [];
  const manifests: any[] = [];
  let jobBody: any;
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/uploads/directories/'))
      chunks.push(request.postDataBuffer()!.length);
    if (request.url().endsWith('/manifest')) manifests.push(request.postDataJSON());
    if (request.method() === 'POST' && request.url().endsWith('/imports'))
      jobBody = request.postDataJSON();
  });
  await openFolder(page);
  await choose(page, root);
  await expect(page.locator('#selected-source')).toContainText(`${basename(root)} · 5`);
  await expect(page.locator('#password-label')).toBeHidden();
  await expect(page.locator('#delete-source')).toBeHidden();
  const imported = page.waitForResponse(
    (r) => r.url().endsWith('/imports') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '导入', exact: true }).click();
  const job = apiData(await (await imported).json());
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  const gamePath = await page.evaluate(
    async (id) => (await (await fetch(`/api/v2/tasks/imports/${id}`)).json()).data.project.path,
    job.task_id,
  );
  expect(await readFile(join(gamePath, 'game/assets/data.bin'))).toEqual(blob);
  expect(await readFile(join(gamePath, 'game/saves/persistent'), 'utf8')).toBe('saved-progress');
  expect(await readFile(join(gamePath, '.hidden'), 'utf8')).toBe('hidden');
  expect((await readFile(join(gamePath, 'empty.dat'))).length).toBe(0);
  expect(manifests).toEqual([
    {
      batch_id: '0',
      files: [
        { path: '.hidden', size_bytes: 6 },
        { path: 'empty.dat', size_bytes: 0 },
        { path: 'game/assets/data.bin', size_bytes: 8388615 },
        { path: 'game/saves/persistent', size_bytes: 14 },
        { path: 'game/script.rpy', size_bytes: 24 },
      ],
    },
  ]);
  expect(chunks).toEqual([6, 8388608, 7, 14, 24]);
  expect(Object.keys(jobBody).sort()).toEqual(['directory_upload_id', 'request_id']);
  expect(await page.evaluate(() => localStorage.getItem('vn-stream:directory-upload'))).toBeNull();
});
test('reload requires same native selection and resumes from confirmed server offset', async ({
  page,
}) => {
  const root = await folder({
    'game/script.rpy': 'label start:\n    return\n',
    'payload.bin': Buffer.alloc(8388611, 7),
  });
  const offsets: number[] = [];
  let hold = true;
  await page.route('**/uploads/directories/*/files/*?offset_bytes=*', async (route) => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    if (route.request().postDataBuffer()!.length === 3 && hold)
      await route.fulfill({
        status: 503,
        json: mockEnvelope(route.request().url(), { detail: 'temporary unavailable' }, 503),
      });
    else {
      if (route.request().postDataBuffer()!.length > 100) offsets.push(offset);
      await route.continue();
    }
  });
  await openFolder(page);
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#import-status')).toContainText('请求失败', { timeout: 10000 });
  await expect(page.locator('#upload-detail')).toContainText('已确认');
  await page.reload();
  await expect(page.locator('#folder-resume')).toBeVisible();
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
  // Same root, count and total bytes; only the relative path changes.
  await rename(join(root, 'payload.bin'), join(root, 'changed.bin'));
  await choose(page, root);
  await expect(page.locator('#import-status')).toContainText('不一致');
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
  await rename(join(root, 'changed.bin'), join(root, 'payload.bin'));
  await choose(page, root);
  hold = false;
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  expect(offsets).toEqual([0]);
});
test('lost XHR response reloads offsets and avoids retransmitting confirmed chunk', async ({
  page,
}) => {
  const root = await folder();
  let chunks = 0;
  await page.route('**/uploads/directories/*/files/*?offset_bytes=*', async (route) => {
    chunks++;
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    await route.fulfill({
      status: 503,
      json: mockEnvelope(route.request().url(), { detail: 'response unavailable' }, 503),
    });
  });
  await openFolder(page);
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  expect(chunks).toBe(1);
});
test('native selection batches more than 1000 files and preserves zero-byte files', async ({
  page,
}) => {
  const data: Record<string, string> = { 'game/script.rpy': 'label start:\n    return\n' };
  for (let i = 0; i < 1001; i++) data[`extra/${String(i).padStart(4, '0')}.dat`] = '';
  const root = await folder(data);
  const batches: number[] = [];
  page.on('request', (r) => {
    if (r.url().endsWith('/manifest')) batches.push(r.postDataJSON().files.length);
  });
  await openFolder(page);
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  expect(batches).toEqual([1000, 2]);
});
test('cancel aborts XHR, retries busy cleanup and clears reload metadata', async ({ page }) => {
  const root = await folder();
  let started = false,
    release!: () => void;
  const gate = new Promise<void>((r) => {
    release = r;
  });
  let releaseCleanup!: () => void;
  const cleanupGate = new Promise<void>((r) => {
    releaseCleanup = r;
  });
  let deletes = 0;
  await page.route('**/uploads/directories/*/files/*?offset_bytes=*', async (route) => {
    started = true;
    await gate;
    await route.fulfill({
      status: 409,
      json: mockEnvelope(route.request().url(), { detail: 'cancelled' }, 409),
    });
  });
  await page.route('**/api/v2/uploads/directories/*', async (route) => {
    if (route.request().method() !== 'DELETE') {
      await route.continue();
      return;
    }
    deletes++;
    if (deletes === 1)
      await route.fulfill({
        status: 409,
        json: mockEnvelope(route.request().url(), { detail: 'busy' }, 409),
      });
    else {
      await cleanupGate;
      await route.continue();
    }
  });
  try {
    await openFolder(page);
    await choose(page, root);
    await page.getByRole('button', { name: '导入', exact: true }).click();
    await expect.poll(() => started).toBe(true);
    await page.getByRole('button', { name: '取消上传' }).click();
    await expect.poll(() => deletes).toBe(2);
    await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
    await expect(page.locator('#choose-folder')).toHaveJSProperty('disabled', true);
    releaseCleanup();
    await expect(page.locator('#import-status')).toHaveText('上传已取消');
    expect(deletes).toBe(2);
    expect(
      await page.evaluate(() => localStorage.getItem('vn-stream:directory-upload')),
    ).toBeNull();
    await page.reload();
    await expect(page.locator('#folder-resume')).toBeHidden();
  } finally {
    release();
    releaseCleanup();
  }
});
test('unsupported directory selection shows archive fallback without flat upload', async ({
  page,
}) => {
  await page.addInitScript(() => {
    delete (HTMLInputElement.prototype as any).webkitdirectory;
  });
  await openFolder(page);
  await expect(page.locator('#folder-unsupported')).toBeVisible();
  await expect(page.locator('#choose-folder')).toHaveJSProperty('disabled', true);
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
});
test('empty folder cannot start, while a project of zero-byte files completes without chunks', async ({
  page,
}) => {
  const empty = await folder({});
  const zero = await folder({ 'game/script.rpy': '', '.zero': '' });
  let puts = 0;
  page.on('request', (r) => {
    if (r.method() === 'PUT') puts++;
  });
  await openFolder(page);
  await page.locator('#folder-file').setInputFiles(empty);
  await expect(page.locator('#import-status')).toContainText('空目录无法上传');
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
  await choose(page, zero);
  await expect(page.locator('#selected-source')).toContainText('2 个文件 · 0 B');
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(zero));
  expect(puts).toBe(0);
});
test('restored directory job uses directory phases and retry without local re-selection or password', async ({
  page,
}) => {
  let current = {
    task_id: 'directory-job',
    request_id: 'stable-request',
    state: 'running',
    phase: 'validating',
    completed_bytes: 4,
    total_bytes: 4,
    error: null,
    warning: null,
    project: null,
    source_name: 'Story',
    upload_id: null,
    directory_upload_id: 'a'.repeat(32),
    delete_source: false,
    abandoned: false,
  };
  let directoryReads = 0;
  let retried: unknown;
  await page.route('**/api/v2/uploads/directories/**', async (route) => {
    directoryReads++;
    await route.fulfill({
      status: 404,
      json: mockEnvelope(route.request().url(), { detail: 'gone' }, 404),
    });
  });
  await page.route(/\/api\/v2\/(?:tasks\/imports\/.*|imports\/active)$/, async (route) => {
    if (route.request().method() === 'POST') {
      retried = route.request().postDataJSON();
      current = { ...current, state: 'running', phase: 'normalizing' };
    }
    await route.fulfill({ json: mockEnvelope(route.request().url(), current, 200) });
  });
  await page.goto('/#add');
  await expect(page.locator('#processing-detail')).toHaveText('正在校验目录');
  await expect(page.locator('#password-label')).toBeHidden();
  await expect(page.getByRole('radio', { name: '上传文件夹', exact: true })).toBeChecked();
  await page.reload();
  for (const [phase, text] of [
    ['normalizing', '正在整理游戏'],
    ['registering', '正在登记游戏'],
    ['cleaning', '正在清理源文件'],
  ]) {
    current = { ...current, phase };
    await expect(page.locator('#processing-detail')).toHaveText(text);
    await expect(page.locator('#processing-progress')).toHaveAttribute(
      'data-indeterminate',
      'true',
    );
  }
  current = { ...current, state: 'failed', phase: 'failed' };
  await expect(page.locator('#processing-detail')).toContainText('目录导入失败');
  await page.getByRole('button', { name: '重试导入' }).click();
  await expect(page.locator('#processing-detail')).toHaveText('正在整理游戏');
  expect(retried).toEqual({});
  expect(directoryReads).toBe(0);
});
test('folder controls and progress fit phone widths in both languages and themes', async ({
  page,
}) => {
  const root = await folder({ 'game/script.rpy': '', 'game/saves/persistent': 'save' });
  const geometry: unknown[] = [];
  await openFolder(page);
  await choose(page, root);
  for (const language of ['zh', 'en']) {
    if (language === 'en') {
      await page.locator('#language-toggle').click();
      await page.locator('#language-en').click();
      await expect(page.getByRole('button', { name: 'Choose folder', exact: true })).toBeVisible();
      await expect(page.locator('#selected-source')).toContainText('2 files');
    }
    for (const width of [320, 390]) {
      await page.setViewportSize({ width, height: 844 });
      for (const theme of ['dark', 'light']) {
        await setAppearance(page, theme);
        await expect(page.locator('#add-game')).not.toBeVisible();
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        const contrast = await page.locator('#selected-source').evaluate((node) => {
          const style = getComputedStyle(node);
          const luminance = (color: string) =>
            color
              .match(/\d+/g)!
              .slice(0, 3)
              .map((channel) => Number(channel) / 255)
              .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
              .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
          const ink = luminance(style.color),
            surface = luminance(getComputedStyle(node.closest('.ant-card')!).backgroundColor);
          return (Math.max(ink, surface) + 0.05) / (Math.min(ink, surface) + 0.05);
        });
        expect(contrast).toBeGreaterThanOrEqual(4.5);
        // Segmented labels must fill equal-width items after a viewport change.
        await expect
          .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
          .toBe(true);
        const boxes = await page
          .locator('#source-tabs .ant-segmented-item')
          .evaluateAll((tabs) => tabs.map((tab) => tab.getBoundingClientRect().width));
        expect(Math.max(...boxes) - Math.min(...boxes)).toBeLessThan(1);
        const button = await page.locator('#choose-folder').boundingBox();
        const card = await page.locator('.import-card').boundingBox();
        expect(button!.x).toBeGreaterThanOrEqual(card!.x);
        expect(button!.x + button!.width).toBeLessThanOrEqual(card!.x + card!.width);
        geometry.push({
          language,
          width,
          theme,
          contrast,
          tabWidths: boxes,
          button,
          card,
          scrollWidth: await page.evaluate(() => document.documentElement.scrollWidth),
        });
      }
    }
    await page.screenshot({ path: `../var/antd-work/folder-${language}.png` });
  }
  await writeFile(
    resolve('../var/antd-work/folder-layout.json'),
    JSON.stringify(geometry, null, 2),
  );
});
test('lost directory create response discovers the existing upload without creating twice', async ({
  page,
}) => {
  const root = await folder();
  let creates = 0,
    discoveries = 0;
  await page.route('**/api/v2/uploads/directories', async (route) => {
    creates++;
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    uploadIds.push(apiData(await response.json()).upload_id);
    await route.fulfill({
      status: 503,
      json: mockEnvelope(route.request().url(), { detail: 'response unavailable' }, 503),
    });
  });
  page.on('request', (r) => {
    if (r.url().endsWith('/uploads/directories/active')) discoveries++;
  });
  await openFolder(page);
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  expect(creates).toBe(1);
  expect(discoveries).toBeGreaterThanOrEqual(2);
});
test('active discovery without browser metadata resumes a partial manifest idempotently', async ({
  page,
  request,
}) => {
  const data: Record<string, string> = { 'game/script.rpy': 'label start:\n    return\n' };
  const files: {
    path: string;
    size_bytes: number;
  }[] = [];
  for (let i = 0; i < 1001; i++) {
    const path = `extra/${String(i).padStart(4, '0')}.dat`;
    data[path] = '';
    files.push({ path, size_bytes: 0 });
  }
  const root = await folder(data);
  const created = await request.post('/api/v2/uploads/directories', {
    data: { name: basename(root), file_count: 1002, total_bytes: 24 },
  });
  expect(created.status()).toBe(201);
  const { upload_id: id } = apiData(await created.json());
  uploadIds.push(id);
  expect(
    (
      await request.post(`/api/v2/uploads/directories/${id}/manifest`, {
        data: { batch_id: '0', files: files.slice(0, 1000) },
      })
    ).status(),
  ).toBe(200);
  const batches: string[] = [];
  page.on('request', (r) => {
    if (r.url().endsWith('/manifest')) batches.push(r.postDataJSON().batch_id);
  });
  // Recovery can finish while the add route has not been drawn yet.
  await page.goto('/');
  await page.getByRole('button', { name: '管理菜单' }).click();
  await expect(page.locator('#active-import')).toBeVisible();
  await page.locator('#active-import').click();
  await expect(page.locator('#folder-resume')).toBeVisible();
  await expect(page.getByRole('radio', { name: '上传文件夹', exact: true })).toBeChecked();
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  expect(batches).toEqual(['0', '1']);
});
test('reloaded large manifest validates metadata beyond the first page before resuming', async ({
  page,
  request,
}) => {
  const data: Record<string, string> = { 'game/script.rpy': 'label start:\n    return\n' };
  const files: {
    path: string;
    size_bytes: number;
  }[] = [];
  for (let i = 0; i < 1001; i++) {
    const path = `extra/${String(i).padStart(4, '0')}.dat`;
    data[path] = '';
    files.push({ path, size_bytes: 0 });
  }
  files.push({ path: 'game/script.rpy', size_bytes: 24 });
  const root = await folder(data);
  const created = await request.post('/api/v2/uploads/directories', {
    data: { name: basename(root), file_count: 1002, total_bytes: 24 },
  });
  expect(created.status()).toBe(201);
  const { upload_id: id } = apiData(await created.json());
  uploadIds.push(id);
  for (const [batch_id, batch] of [
    ['0', files.slice(0, 1000)],
    ['1', files.slice(1000)],
  ] as const)
    expect(
      (
        await request.post(`/api/v2/uploads/directories/${id}/manifest`, {
          data: { batch_id, files: batch },
        })
      ).status(),
    ).toBe(200);
  expect((await request.post(`/api/v2/uploads/directories/${id}/seal`)).status()).toBe(200);
  const pages: string[] = [];
  page.on('request', (r) => {
    if (r.method() === 'GET' && r.url().includes(`/uploads/directories/${id}?`))
      pages.push(new URL(r.url()).searchParams.get('cursor')!);
  });
  await page.goto('/#add');
  await expect(page.locator('#folder-resume')).toBeVisible();
  await rename(join(root, 'game/script.rpy'), join(root, 'game/changed.rpy'));
  await choose(page, root);
  await expect(page.locator('#import-status')).toContainText('不一致');
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
  expect(pages).toContain('1000');
  await rename(join(root, 'game/changed.rpy'), join(root, 'game/script.rpy'));
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
});
test('lost directory job response retries the stable request without retransmitting', async ({
  page,
}) => {
  const root = await folder();
  const bodies: unknown[] = [];
  let chunks = 0;
  page.on('request', (r) => {
    if (r.method() === 'PUT') chunks++;
  });
  await page.route('**/api/v2/imports/active', (route) => route.fulfill({ json: { data: null } }));
  await page.route('**/api/v2/imports', async (route) => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) {
      const response = await route.fetch();
      expect(response.status()).toBe(202);
      await route.fulfill({
        status: 503,
        json: mockEnvelope(route.request().url(), { detail: 'response unavailable' }, 503),
      });
    } else await route.continue();
  });
  await openFolder(page);
  await choose(page, root);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await page.getByRole('button', { name: '重试请求', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(basename(root));
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(chunks).toBe(1);
});
test('transient active 404 does not prevent a new directory selection', async ({ page }) => {
  await page.route('**/api/v2/uploads/directories/active', (route) =>
    route.fulfill({
      status: 404,
      json: mockEnvelope(route.request().url(), { detail: 'gone' }, 404),
    }),
  );
  await openFolder(page);
  await expect(page.locator('#choose-folder')).toHaveJSProperty('disabled', false);
  await expect(page.locator('#connection-status')).toBeEmpty();
});
test('manifest timeout reports a retryable failure instead of appearing cancelled', async ({
  page,
}) => {
  const root = await folder();
  let started = false,
    release!: () => void;
  const gate = new Promise<void>((r) => {
    release = r;
  });
  await page.route('**/uploads/directories/*/manifest', async (route) => {
    started = true;
    await gate;
    await route.fulfill({
      status: 503,
      json: mockEnvelope(route.request().url(), { detail: 'timeout' }, 503),
    });
  });
  try {
    await openFolder(page);
    await choose(page, root);
    await page.clock.install();
    await page.getByRole('button', { name: '导入', exact: true }).click();
    await expect.poll(() => started).toBe(true);
    await page.clock.fastForward(5100);
    await expect(page.locator('#import-status')).toContainText('请求超时');
    await expect(page.locator('#start-import')).toHaveJSProperty('disabled', false);
  } finally {
    release();
  }
});
