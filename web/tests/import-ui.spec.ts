import {
  mockEnvelope,
  apiData,
  engineFixtures,
  renpyProjectFixture,
  runtimeFixture,
} from './api-fixtures.js';
import { test, expect, setAppearance, type Page, type Route } from './fixtures.js';
const game = renpyProjectFixture({
  project_id: 'a'.repeat(32),
  name: 'Moonlit Story',
  path: '/projects/story',
  engine_version: '8.3.6',
  release_version: '1.2',
  size_bytes: 805306368,
  play_seconds: 120,
  runtime_override: 'renpy:8.3.7',
  runtime_options: [runtimeFixture('8.3.7'), runtimeFixture('8.4.1')],
  recommendation: {
    build_version: '8.3.7',
    kind: 'newer',
    runtime_id: 'renpy:8.3.7',
    display_name: null,
  },
});
const task = (extra: Record<string, unknown> = {}) => ({
  task_id: 'job-one',
  request_id: 'request-one',
  state: 'running',
  phase: 'scanning',
  completed_bytes: 0,
  total_bytes: null,
  error: null,
  warning: null,
  project: null,
  source_name: 'story.zip',
  upload_id: null,
  delete_source: false,
  abandoned: false,
  ...extra,
});
async function mock(page: Page, handler?: (path: string, route: Route) => Promise<boolean>) {
  await page.route('**/api/v2/**', async (route) => {
    const path = new URL(route.request().url()).pathname.split('/api/v2/')[1];
    if (await handler?.(path, route)) return;
    const data: Record<string, unknown> = {
      projects: [game],

      session: { state: 'stopped' },
      'projects/refresh': [game],
      'imports/active': null,
      'uploads/directories/active': null,
      engines: engineFixtures,
    };
    await route.fulfill({
      status: path in data ? 200 : 500,
      json: mockEnvelope(
        route.request().url(),
        path in data ? data[path] : { detail: 'Unexpected endpoint' },
        path in data ? 200 : 500,
      ),
    });
  });
}
async function add(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: '管理菜单' }).click();
  await page.locator('#add-game').click();
  await expect(page).toHaveURL(/#add$/);
}
async function selectRoot(page: Page) {
  await page.locator('#source-roots').click();
  await page.getByRole('option', { name: 'Games', exact: true }).click();
}
async function server(page: Page) {
  await page.getByRole('radio', { name: '服务器文件', exact: true }).locator('..').click();
  await selectRoot(page);
}
async function file(page: Page) {
  await page
    .getByLabel('选择文件', { exact: true })
    .setInputFiles({ name: 'story.zip', mimeType: 'application/zip', buffer: Buffer.from('test') });
}
async function uploadMock(path: string, route: Route) {
  if (path === 'uploads/archives') {
    await route.fulfill({
      status: 201,
      json: mockEnvelope(route.request().url(), { upload_id: 'upload-one', offset_bytes: 0 }, 201),
    });
    return true;
  }
  if (path === 'uploads/archives/upload-one') {
    await route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { upload_id: 'upload-one', offset_bytes: route.request().method() === 'PUT' ? 4 : 0 },
        200,
      ),
    });
    return true;
  }
  return false;
}
async function browseMock(path: string, route: Route, directory = false) {
  if (path !== 'imports/sources') return false;
  await route.fulfill({
    json: mockEnvelope(
      route.request().url(),
      new URL(route.request().url()).searchParams.has('root')
        ? {
            roots: [{ id: 0, name: 'Games' }],
            path: '',
            source: '/allowed',
            project: directory,
            entries: directory
              ? []
              : [{ name: 'story.zip', path: 'story.zip', directory: false, project: false }],
          }
        : { roots: [{ id: 0, name: 'Games' }], entries: [] },
      200,
    ),
  });
  return true;
}
test('mobile library, anchored menu, independent add route and format tags', async ({ page }) => {
  await mock(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
  await page.screenshot({ path: '../var/antd-work/mobile-dark-library.png' });
  const box = await page.locator('#catalog').boundingBox();
  await page.getByRole('button', { name: '管理菜单' }).click();
  await expect(page.locator('#add-game')).toBeVisible();
  expect(await page.locator('#catalog').boundingBox()).toEqual(box);
  await page.locator('#add-game').click();
  await expect(page.locator('#catalog')).toBeHidden();
  await expect(page.locator('.format-tags .ant-tag')).toHaveText([
    'ZIP',
    '7Z',
    'RAR',
    'TAR',
    'TAR.GZ',
    'TAR.BZ2',
    'TAR.XZ',
    'APK',
  ]);
  await expect(page.locator('#add-page')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../var/antd-work/mobile-dark-add.png' });
  await page.goBack();
  await expect(page.locator('#catalog')).toBeVisible();
  await page.goForward();
  await expect(page.locator('#add-page')).toBeVisible();
});
test('immediate language, accessible submenu and light preference persist', async ({ page }) => {
  await mock(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#projects/${game.project_id}`);
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await expect(page.locator('h1')).toHaveText('Game details');
  await expect(page.locator('#language-toggle')).toBeFocused();
  await expect(page.getByLabel('Engine version', { exact: true })).toBeVisible();
  await setAppearance(page, 'light');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('h1')).toHaveText('Game details');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', { name: 'Back to library' }).click();
  await page.screenshot({ path: '../var/antd-work/mobile-light-library.png' });
  await page.getByRole('button', { name: 'Manage library' }).click();
  await page.locator('#add-game').click();
  await expect(page.getByLabel('Archive password (optional)')).toBeVisible();
  await page.waitForTimeout(400);
  await page.screenshot({ path: '../var/antd-work/mobile-light-add.png' });
});
test('return toast expires without polling resetting it', async ({ page }) => {
  await mock(page);
  await page.addInitScript(() => sessionStorage.setItem('vn-stream:return', '游戏已退出'));
  await page.goto('/');
  await expect(page.locator('#message')).toHaveText('游戏已退出');
  await page.waitForTimeout(5300);
  await expect(page.locator('#message')).toBeEmpty();
});
test('failed upload retries password against same job without retransmitting bytes', async ({
  page,
}) => {
  let chunks = 0,
    creates = 0;
  const passwords: string[] = [];
  let current = task({
    state: 'failed',
    phase: 'failed',
    error: '密码错误',
    upload_id: 'upload-one',
  });
  await mock(page, async (path, route) => {
    if (path === 'uploads/archives/upload-one' && route.request().method() === 'PUT') chunks++;
    if (await uploadMock(path, route)) return true;
    if (path === 'imports') {
      creates++;
      passwords.push(route.request().postDataJSON().password);
      await route.fulfill({ status: 202, json: mockEnvelope(route.request().url(), current, 202) });
      return true;
    }
    if (path === 'tasks/imports/job-one/retry') {
      passwords.push(route.request().postDataJSON().password);
      current = task({ state: 'completed', phase: 'completed', project: game }) as typeof current;
      await route.fulfill({ status: 202, json: mockEnvelope(route.request().url(), current, 202) });
      return true;
    }
    return false;
  });
  await add(page);
  await file(page);
  await page.getByLabel('解压密码（可选）').fill('wrong');
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试导入' })).toBeEnabled();
  await page.getByLabel('解压密码（可选）').fill('correct');
  await page.getByRole('button', { name: '重试导入' }).click();
  await expect(page.locator('#details h2')).toHaveText(game.name);
  expect(chunks).toBe(1);
  expect(creates).toBe(1);
  expect(passwords).toEqual(['wrong', 'correct']);
  await expect(page.locator('input[type=password]:visible')).toHaveCount(0);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('correct');
});
for (const source of ['upload', 'server'])
  test(`stale folder record preserves restored ${source} archive password retry`, async ({
    page,
  }) => {
    let sent: unknown;
    const current = task({
      state: 'failed',
      phase: 'failed',
      error: 'Password required',
      upload_id: source === 'upload' ? 'old-archive' : null,
    });
    await page.addInitScript(() =>
      localStorage.setItem(
        'vn-stream:directory-upload',
        JSON.stringify({ id: 'f'.repeat(32), requestId: 'stale-folder-request' }),
      ),
    );
    await mock(page, async (path, route) => {
      if (path === 'imports/active' || path === 'tasks/imports/job-one') {
        await route.fulfill({ json: mockEnvelope(route.request().url(), current, 200) });
        return true;
      }
      if (path === 'tasks/imports/job-one/retry') {
        sent = route.request().postDataJSON();
        await route.fulfill({
          status: 202,
          json: mockEnvelope(route.request().url(), current, 202),
        });
        return true;
      }
      return false;
    });
    await page.goto('/#add');
    await expect(page.getByLabel('解压密码（可选）')).toBeVisible();
    await page.getByLabel('解压密码（可选）').fill('fixture-retry-password');
    await page.getByRole('button', { name: '重试导入' }).click();
    await expect.poll(() => sent).toEqual({ password: 'fixture-retry-password' });
    await expect(
      page.getByRole('radio', {
        name: source === 'upload' ? '上传文件' : '服务器文件',
        exact: true,
      }),
    ).toBeChecked();
    expect(
      await page.evaluate(() => localStorage.getItem('vn-stream:directory-upload')),
    ).toBeNull();
  });
test('cancel aborts active XHR chunk and retries busy cleanup', async ({ page }) => {
  let deletes = 0,
    started = false;
  await mock(page, async (path, route) => {
    if (path === 'uploads/archives/upload-one') {
      if (route.request().method() === 'DELETE') {
        deletes++;
        await route.fulfill({
          status: deletes === 1 ? 409 : 200,
          json: mockEnvelope(route.request().url(), { cancelled: true }, deletes === 1 ? 409 : 200),
        });
        return true;
      }
      if (route.request().method() === 'PUT') {
        started = true;
        await new Promise((r) => setTimeout(r, 600));
        await route
          .fulfill({
            json: mockEnvelope(
              route.request().url(),
              { upload_id: 'upload-one', offset_bytes: 4 },
              200,
            ),
          })
          .catch(() => {});
        return true;
      }
    }
    return uploadMock(path, route);
  });
  await add(page);
  await file(page);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect.poll(() => started).toBe(true);
  await page.getByRole('button', { name: '取消上传' }).click();
  await expect(page.locator('#import-status')).toHaveText('上传已取消');
  expect(deletes).toBe(2);
});
for (const deletion of [false, true])
  test(`server archive requires selection and explicit start; delete_source=${deletion}`, async ({
    page,
  }) => {
    let body: any;
    await mock(page, async (path, route) => {
      if (await browseMock(path, route)) return true;
      if (path === 'imports') {
        body = route.request().postDataJSON();
        await route.fulfill({
          status: 202,
          json: mockEnvelope(
            route.request().url(),
            task({ state: 'completed', phase: 'completed', project: game }),
            202,
          ),
        });
        return true;
      }
      return false;
    });
    await add(page);
    await server(page);
    await page.getByRole('button', { name: 'story.zip', exact: true }).click();
    expect(body).toBeUndefined();
    await expect(page.locator('#delete-source')).toHaveJSProperty('checked', false);
    if (deletion) await page.locator('#delete-source').click();
    await page.getByRole('button', { name: '导入', exact: true }).click();
    await expect(page.locator('#details h2')).toHaveText(game.name);
    expect(body.path).toBe('/allowed/story.zip');
    expect(body.delete_source).toBe(deletion);
  });
test('directory registration has no delete option and prevents duplicate submission', async ({
  page,
}) => {
  let count = 0,
    release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  await mock(page, async (path, route) => {
    if (await browseMock(path, route, true)) return true;
    if (path === 'imports') {
      count++;
      expect(route.request().postDataJSON().delete_source).toBe(false);
      await gate;
      await route.fulfill({
        status: 202,
        json: mockEnvelope(
          route.request().url(),
          task({ state: 'completed', phase: 'completed', project: game }),
          202,
        ),
      });
      return true;
    }
    return false;
  });
  await add(page);
  await server(page);
  await page.getByRole('button', { name: '导入当前游戏目录' }).click();
  await expect(page.locator('#delete-source')).toBeHidden();
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
  expect(count).toBe(1);
  release();
  await expect(page.locator('#details h2')).toHaveText(game.name);
});
test('review: late server response cannot replace upload tab and server reentry recovers', async ({
  page,
}) => {
  let release!: () => void,
    requested = false,
    filesCalls = 0;
  const gate = new Promise<void>((r) => (release = r));
  await mock(page, async (path, route) => {
    if (path !== 'imports/sources') return false;
    requested = true;
    filesCalls++;
    if (filesCalls === 1) await gate;
    await route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { roots: [{ id: 0, name: 'Late root' }], entries: [] },
        200,
      ),
    });
    return true;
  });
  await add(page);
  await page.getByRole('radio', { name: '服务器文件', exact: true }).locator('..').click();
  await expect.poll(() => requested).toBe(true);
  await page.getByRole('radio', { name: '上传文件', exact: true }).locator('..').click();
  release();
  await expect(page.locator('#choose-file')).toBeVisible();
  await expect(page.locator('#source-roots')).toHaveCount(0);
  await page.getByRole('radio', { name: '服务器文件', exact: true }).locator('..').click();
  await expect(page.locator('#source-roots')).toBeVisible();
  expect(filesCalls).toBe(2);
});
for (const source of ['upload', 'server'])
  test(`${source} completed game survives catalog refresh failure`, async ({ page }) => {
    let posted = false,
      failures = 0;
    await mock(page, async (path, route) => {
      if (path === 'projects' && posted) {
        failures++;
        await route.fulfill({
          status: 503,
          json: mockEnvelope(route.request().url(), { detail: 'Catalog unavailable' }, 503),
        });
        return true;
      }
      if ((await uploadMock(path, route)) || (await browseMock(path, route, true))) return true;
      if (path === 'imports') {
        posted = true;
        await route.fulfill({
          status: 202,
          json: mockEnvelope(
            route.request().url(),
            task({ state: 'completed', phase: 'completed', project: game }),
            202,
          ),
        });
        return true;
      }
      return false;
    });
    await add(page);
    if (source === 'upload') await file(page);
    else {
      await server(page);
      await page.getByRole('button', { name: '导入当前游戏目录' }).click();
    }
    await page.getByRole('button', { name: '导入', exact: true }).click();
    await expect(page.locator('#details h2')).toHaveText(game.name);
    await expect.poll(() => failures).toBeGreaterThan(0);
    await expect(page.locator('#message')).toContainText('游戏已添加');
    await page.getByRole('button', { name: '返回游戏库' }).click();
    await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
    await expect(page.locator('#add-page')).toBeHidden();
    await expect(page.locator('#archive-password')).toHaveValue('');
  });
test('unknown engine explicit choice and recommendation sends automatic null', async ({ page }) => {
  let sent: unknown;
  await mock(page, async (path, route) => {
    if (path === `projects/${game.project_id}/start`) {
      sent = route.request().postDataJSON().runtime_id;
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          { state: 'running', project_id: game.project_id },
          200,
        ),
      });
      return true;
    }
    if (path === 'stream') {
      await route.fulfill({
        json: mockEnvelope(route.request().url(), { available: false, url: null }, 200),
      });
      return true;
    }
    return false;
  });
  await page.goto(`/#projects/${game.project_id}`);
  await expect(page.locator('#details .vn-select-value')).toContainText('推荐');
  await page.getByRole('button', { name: '开始', exact: true }).click();
  await expect.poll(() => sent).toBeNull();
});
test('English preserves raw game metadata', async ({ page }) => {
  await mock(page, async (path, route) => {
    if (path !== 'projects') return false;
    await route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        [{ ...game, name: '开始', release_version: '未知' }],
        200,
      ),
    });
    return true;
  });
  await page.addInitScript(() =>
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        language: 'en',
      }),
    ),
  );
  await page.goto('/');
  await page.getByRole('button', { name: '开始', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText('开始');
  await expect(page.locator('.hero .vn-game-version')).toHaveText('未知');
});
test('real phases, connection recovery, task resume and stable form across catalog polling', async ({
  page,
}) => {
  let current = task(),
    gets = 0,
    inFlight = 0,
    max = 0,
    offline = false;
  await mock(page, async (path, route) => {
    if (await uploadMock(path, route)) return true;
    if (path === 'imports') {
      current = task({
        phase: 'extracting',
        completed_bytes: 25,
        total_bytes: 100,
        upload_id: 'upload-one',
      });
      await route.fulfill({ status: 202, json: mockEnvelope(route.request().url(), current, 202) });
      return true;
    }
    if (path === 'tasks/imports/job-one') {
      gets++;
      inFlight++;
      max = Math.max(max, inFlight);
      await new Promise((r) => setTimeout(r, 20));
      inFlight--;
      await route.fulfill({
        status: offline ? 503 : 200,
        json: mockEnvelope(
          route.request().url(),
          offline ? { detail: 'Offline' } : current,
          offline ? 503 : 200,
        ),
      });
      return true;
    }
    return false;
  });
  await add(page);
  await file(page);
  await page.getByLabel('解压密码（可选）').fill('stable');
  await page.waitForTimeout(5200);
  await expect(page.getByLabel('解压密码（可选）')).toHaveValue('stable');
  await expect(page.locator('#selected-source')).toContainText('story.zip');
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#processing-progress').getByRole('progressbar')).toHaveAttribute(
    'aria-valuenow',
    '25',
  );
  await expect(page.locator('#processing-detail')).toContainText('25 B / 100 B');
  current = task({ phase: 'normalizing', completed_bytes: 100, total_bytes: 100 });
  await expect(page.locator('#processing-progress')).toHaveAttribute('data-indeterminate', 'true');
  offline = true;
  await expect(page.locator('#connection-status')).toContainText('连接中断');
  await expect(page.locator('#start-import')).toBeHidden();
  offline = false;
  await page.getByRole('button', { name: '返回游戏库' }).click();
  await page.getByRole('button', { name: '管理菜单' }).click();
  await page.locator('#active-import').click();
  await expect(page.locator('#selected-source')).toContainText('story.zip');
  await expect.poll(() => gets).toBeGreaterThan(2);
  expect(max).toBe(1);
});
test('reload restores failed job with fixed cleanup policy; abandon deletes job only', async ({
  page,
}) => {
  let abandoned = false;
  await mock(page, async (path, route) => {
    if (path === 'imports/active') {
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          abandoned
            ? null
            : task({
                state: 'failed',
                phase: 'failed',
                error: 'Password required',
                delete_source: true,
                upload_id: 'old-upload',
              }),
          200,
        ),
      });
      return true;
    }
    if (path === 'tasks/imports/job-one' && route.request().method() === 'DELETE') {
      abandoned = true;
      await route.fulfill({ json: mockEnvelope(route.request().url(), { abandoned: true }, 200) });
      return true;
    }
    if (path === 'tasks/imports/job-one') {
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          task({
            state: 'failed',
            phase: 'failed',
            delete_source: true,
            upload_id: 'old-upload',
          }),
          200,
        ),
      });
      return true;
    }
    return false;
  });
  await page.goto('/#add');
  await expect(page.getByRole('button', { name: '重试导入' })).toBeVisible();
  await expect(page.locator('#selected-source')).toContainText('成功后删除服务器源文件');
  await expect(page.locator('#delete-source')).toBeHidden();
  await page.reload();
  await expect(page.getByRole('button', { name: '重试导入' })).toBeVisible();
  await page.getByRole('button', { name: '放弃任务' }).click();
  await expect(page.locator('#choose-file')).toBeEnabled();
  expect(abandoned).toBe(true);
});
test('retry conflict retains task diagnostics and source', async ({ page }) => {
  let creates = 0;
  await mock(page, async (path, route) => {
    if (path === 'imports/active') {
      await route.fulfill({
        json: mockEnvelope(route.request().url(), task({ state: 'failed', phase: 'failed' }), 200),
      });
      return true;
    }
    if (path === 'tasks/imports/job-one/retry') {
      await route.fulfill({
        status: 409,
        json: mockEnvelope(
          route.request().url(),
          {
            detail: 'Target cannot be verified',
            messages: {
              detail: {
                code: 'errors.theReservedDestinationExistsOrItsStateIsUncertain',
                params: {},
              },
            },
          },
          409,
        ),
      });
      return true;
    }
    if (path === 'imports') {
      creates++;
      return true;
    }
    if (path === 'tasks/imports/job-one') {
      await route.fulfill({
        json: mockEnvelope(route.request().url(), task({ state: 'failed', phase: 'failed' }), 200),
      });
      return true;
    }
    return false;
  });
  await page.goto('/#add');
  await page.getByRole('button', { name: '重试导入' }).click();
  await expect(page.locator('#import-status')).toContainText('预留发布目录已存在或状态无法确认');
  await expect(page.locator('#selected-source')).toContainText('story.zip');
  expect(creates).toBe(0);
});
test('local components and icons issue no external asset requests; mobile server screenshot', async ({
  page,
  baseURL,
}) => {
  const appOrigin = new URL(baseURL!).origin;
  const external: string[] = [];
  page.on('request', (r) => {
    const url = new URL(r.url());
    if (url.origin !== appOrigin && url.protocol !== 'data:') external.push(url.href);
  });
  await mock(page, async (path, route) => browseMock(path, route));
  await page.setViewportSize({ width: 390, height: 844 });
  await add(page);
  await server(page);
  await expect(page.locator('.file-entry')).toBeVisible();
  await page.screenshot({ path: '../var/antd-work/mobile-dark-server.png' });
  await setAppearance(page, 'light');
  await page.waitForTimeout(400);
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'light');
  await expect(page.locator('#server-source .ant-select')).toHaveCSS(
    'background-color',
    'rgb(255, 255, 255)',
  );
  await page.screenshot({ path: '../var/antd-work/mobile-light-server.png' });
  expect(external).toEqual([]);
  expect(await page.locator('svg.lucide').count()).toBeGreaterThan(0);
});
test('unknown recommendation requires manual engine choice', async ({ page }) => {
  let runtime: unknown;
  await mock(page, async (path, route) => {
    if (path === 'projects') {
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          [
            {
              ...game,
              runtime_override: null,
              recommendation: { build_version: null, kind: 'unknown', runtime_id: null },
            },
          ],
          200,
        ),
      });
      return true;
    }
    if (path === `projects/${game.project_id}/start`) {
      runtime = route.request().postDataJSON().runtime_id;
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          { state: 'running', project_id: game.project_id },
          200,
        ),
      });
      return true;
    }
    if (path === 'stream') {
      await route.fulfill({
        json: mockEnvelope(route.request().url(), { available: false, url: null }, 200),
      });
      return true;
    }
    return false;
  });
  await page.goto(`/#projects/${game.project_id}`);
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeDisabled();
  await page.getByLabel('引擎版本').click();
  await page.getByRole('option', { name: '8.4.1', exact: true }).click();
  await page.getByRole('button', { name: '开始', exact: true }).click();
  await expect.poll(() => runtime).toBe('renpy:8.4.1');
});
test('lost chunk response synchronizes confirmed offset before sending remaining bytes', async ({
  page,
}) => {
  let puts = 0,
    confirmed = 0;
  const offsets: string[] = [];
  await mock(page, async (path, route) => {
    if (path === 'uploads/archives') {
      await route.fulfill({
        status: 201,
        json: mockEnvelope(
          route.request().url(),
          { upload_id: 'upload-one', offset_bytes: 0 },
          201,
        ),
      });
      return true;
    }
    if (path === 'uploads/archives/upload-one') {
      if (route.request().method() === 'PUT') {
        puts++;
        offsets.push(new URL(route.request().url()).searchParams.get('offset_bytes')!);
        confirmed = 4;
        await route.fulfill({
          status: 503,
          json: mockEnvelope(route.request().url(), { detail: 'lost response' }, 503),
        });
        return true;
      }
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          { upload_id: 'upload-one', offset_bytes: confirmed },
          200,
        ),
      });
      return true;
    }
    if (path === 'imports') {
      await route.fulfill({
        status: 202,
        json: mockEnvelope(
          route.request().url(),
          task({ state: 'completed', phase: 'completed', project: game }),
          202,
        ),
      });
      return true;
    }
    return false;
  });
  await add(page);
  await file(page);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(game.name);
  expect(puts).toBe(1);
  expect(offsets).toEqual(['0']);
});
test('lost create response retries identical request id without new upload', async ({ page }) => {
  let posts = 0,
    uploads = 0;
  const ids: string[] = [];
  await mock(page, async (path, route) => {
    if (path === 'uploads/archives') uploads++;
    if (await uploadMock(path, route)) return true;
    if (path === 'imports') {
      posts++;
      ids.push(route.request().postDataJSON().request_id);
      await route.fulfill({
        status: posts === 1 ? 503 : 202,
        json: mockEnvelope(
          route.request().url(),
          posts === 1
            ? { detail: 'response lost' }
            : task({ state: 'completed', phase: 'completed', project: game }),
          posts === 1 ? 503 : 202,
        ),
      });
      return true;
    }
    return false;
  });
  await add(page);
  await file(page);
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await page.getByRole('button', { name: '重试请求', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(game.name);
  expect(ids).toHaveLength(2);
  expect(ids[0]).toBe(ids[1]);
  expect(uploads).toBe(1);
});
test('running job restores after reload and completes only after cleaning', async ({ page }) => {
  let current = task({ phase: 'cleaning', completed_bytes: 100, total_bytes: 100 });
  await mock(page, async (path, route) => {
    if (path === 'imports/active' || path === 'tasks/imports/job-one') {
      await route.fulfill({ json: mockEnvelope(route.request().url(), current, 200) });
      return true;
    }
    return false;
  });
  await page.goto('/#add');
  await expect(page.locator('#processing-detail')).toHaveText('正在清理源文件');
  await expect(page.locator('#processing-progress')).toHaveAttribute('data-indeterminate', 'true');
  await page.reload();
  await expect(page.locator('#processing-detail')).toHaveText('正在清理源文件');
  await expect(page.locator('main > #details')).toBeHidden();
  current = task({ state: 'completed', phase: 'completed', project: game });
  await expect(page.locator('#details h2')).toHaveText(game.name);
});
test('review: foreground aborts held initial restore and ignores its late result', async ({
  page,
}) => {
  let calls = 0,
    release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  await page.addInitScript(() => {
    const nativeFetch = window.fetch;
    const tracker = { active: 0, max: 0, aborted: 0 };
    (window as any).statusTracker = tracker;
    window.fetch = async (input, init) => {
      if (!/api\/v2\/(tasks\/imports\/|imports\/active)/.test(String(input)))
        return nativeFetch(input, init);
      tracker.active++;
      tracker.max = Math.max(tracker.max, tracker.active);
      let ended = false;
      const end = () => {
        if (!ended) {
          ended = true;
          tracker.active--;
        }
      };
      init?.signal?.addEventListener(
        'abort',
        () => {
          tracker.aborted++;
          end();
        },
        { once: true },
      );
      try {
        return await nativeFetch(input, init);
      } finally {
        end();
      }
    };
  });
  await mock(page, async (path, route) => {
    if (path !== 'imports/active') return false;
    calls++;
    if (calls === 1) {
      await gate;
      await route
        .fulfill({
          json: mockEnvelope(
            route.request().url(),
            task({ state: 'failed', phase: 'failed' }),
            200,
          ),
        })
        .catch(() => {});
    } else await route.fulfill({ json: mockEnvelope(route.request().url(), null, 200) });
    return true;
  });
  await page.goto('/#add');
  await file(page);
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', true);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => calls, { timeout: 1500 }).toBe(2);
  await expect(page.locator('#start-import')).toHaveJSProperty('disabled', false);
  release();
  await page.waitForTimeout(100);
  await expect(page.getByRole('button', { name: '重试导入' })).toHaveCount(0);
  const tracker = await page.evaluate(() => (window as any).statusTracker);
  expect(tracker.max).toBe(1);
  expect(tracker.aborted).toBe(1);
});
test('review: held running status times out then recovers with one live poll', async ({ page }) => {
  let calls = 0,
    release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  await mock(page, async (path, route) => {
    if (path === 'imports/active') {
      await route.fulfill({ json: mockEnvelope(route.request().url(), task(), 200) });
      return true;
    }
    if (path === 'tasks/imports/job-one') {
      calls++;
      if (calls === 1) {
        await gate;
        await route
          .fulfill({
            json: mockEnvelope(
              route.request().url(),
              task({ phase: 'extracting', completed_bytes: 1, total_bytes: 100 }),
              200,
            ),
          })
          .catch(() => {});
      } else
        await route.fulfill({
          json: mockEnvelope(
            route.request().url(),
            task({ phase: 'extracting', completed_bytes: 50, total_bytes: 100 }),
            200,
          ),
        });
      return true;
    }
    return false;
  });
  await page.goto('/#add');
  await expect.poll(() => calls).toBe(1);
  await expect(page.locator('#connection-status')).toContainText('连接中断', { timeout: 6500 });
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.locator('#processing-progress').getByRole('progressbar')).toHaveAttribute(
    'aria-valuenow',
    '50',
  );
  release();
  await page.waitForTimeout(100);
  await expect(page.locator('#processing-progress').getByRole('progressbar')).toHaveAttribute(
    'aria-valuenow',
    '50',
  );
});
test('review: unresolved server create locks the captured deletion policy', async ({ page }) => {
  let posts = 0;
  const values: boolean[] = [];
  const ids: string[] = [];
  await mock(page, async (path, route) => {
    if (await browseMock(path, route)) return true;
    if (path === 'imports') {
      posts++;
      const body = route.request().postDataJSON();
      values.push(body.delete_source);
      ids.push(body.request_id);
      await route.fulfill({
        status: posts === 1 ? 503 : 202,
        json: mockEnvelope(
          route.request().url(),
          posts === 1
            ? { detail: 'lost response' }
            : task({ state: 'completed', phase: 'completed', project: game }),
          posts === 1 ? 503 : 202,
        ),
      });
      return true;
    }
    return false;
  });
  await add(page);
  await server(page);
  await page.getByRole('button', { name: 'story.zip', exact: true }).click();
  await page.locator('#delete-source').click();
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试请求', exact: true })).toBeEnabled();
  await expect(page.locator('#delete-source')).toHaveJSProperty('disabled', true);
  await expect(page.locator('#delete-source')).toHaveJSProperty('checked', true);
  await page.getByRole('button', { name: '重试请求', exact: true }).click();
  await expect(page.locator('#details h2')).toHaveText(game.name);
  expect(values).toEqual([true, true]);
  expect(ids[0]).toBe(ids[1]);
});
test('directory navigation cannot select stale entries during a delayed or failed browse', async ({
  page,
}) => {
  let release!: () => void,
    fail = true,
    posts = 0;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const roots = [
    { id: 0, name: 'Games' },
    { id: 1, name: 'Next' },
  ];
  await mock(page, async (path, route) => {
    if (path === 'imports/sources') {
      const root = new URL(route.request().url()).searchParams.get('root');
      if (root === null)
        await route.fulfill({
          json: mockEnvelope(route.request().url(), { roots, entries: [] }, 200),
        });
      else if (root === '0')
        await route.fulfill({
          json: mockEnvelope(
            route.request().url(),
            {
              roots,
              source: '/root-a',
              path: '',
              project: true,
              entries: [{ name: 'old.zip', path: 'old.zip', directory: false }],
            },
            200,
          ),
        });
      else {
        await gate;
        await route.fulfill(
          fail
            ? { status: 503, json: { detail: 'unavailable' } }
            : {
                json: mockEnvelope(route.request().url(), {
                  roots,
                  source: '/root-b',
                  path: '',
                  project: true,
                  entries: [],
                }),
              },
        );
      }
      return true;
    }
    if (path === 'imports') {
      posts++;
      expect(route.request().postDataJSON().path).toBe('/root-b');
      await route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          task({ state: 'completed', phase: 'completed', project: game }),
          200,
        ),
      });
      return true;
    }
    return false;
  });
  try {
    await add(page);
    await server(page);
    await expect(page.locator('#select-directory')).toBeEnabled();
    await page.locator('#source-roots').click();
    await page.getByRole('option', { name: 'Next', exact: true }).click();
    await expect(page.locator('#select-directory')).toBeHidden();
    await expect(page.getByRole('button', { name: 'old.zip', exact: true })).toHaveCount(0);
    await expect(page.locator('#start-import')).toBeDisabled();
    release();
    await expect(page.locator('#server-source')).toContainText('请求失败');
    await expect(page.locator('#select-directory')).toBeHidden();
    expect(posts).toBe(0);
    fail = false;
    await page.locator('.browser-path').getByRole('button', { name: '/', exact: true }).click();
    await page.locator('#select-directory').click();
    await page.locator('#start-import').click();
    await expect.poll(() => posts).toBe(1);
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});
