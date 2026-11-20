import {
  mockEnvelope,
  apiData,
  engineFixtures,
  renpyProjectFixture,
  runtimeFixture,
} from './api-fixtures.js';
import { test, expect } from './fixtures.js';
test('top preferences preserve the selected file and archive password when language changes', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto('/');
  const language = page.locator('#language-toggle');
  await expect(language).toContainText('中文');
  const controls = await page.locator('#top-controls').boundingBox();
  const management = await page.locator('#menu-toggle').boundingBox();
  expect(controls).not.toBeNull();
  expect(management).not.toBeNull();
  expect(Math.abs(management!.x + management!.width - controls!.x - controls!.width)).toBeLessThan(
    1,
  );
  await language.click();
  await page.locator('#language-en').click();
  await expect(page.locator('h1')).toHaveText('My games');
  await expect(language).toContainText('English');
  const dark = page.locator('#theme-toggle');
  await expect(dark).toHaveAttribute('aria-label', 'Dark mode');
  await dark.click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.locator('#menu-toggle').click();
  await page.locator('#add-game').click();
  await page.locator('#archive-password').fill('keep-this');
  await page.locator('#archive-file').setInputFiles({
    name: 'story.zip',
    mimeType: 'application/zip',
    buffer: Buffer.from('self authored'),
  });
  await language.click();
  await page.locator('#language-zh').click();
  await expect(page.locator('#archive-password')).toHaveValue('keep-this');
  expect(
    await page.locator('#archive-file').evaluate((el: HTMLInputElement) => el.files?.[0]?.name),
  ).toBe('story.zip');
  await expect(page.locator('h1')).toHaveText('添加游戏');
  expect(await page.locator('body').evaluate((el) => el.scrollWidth)).toBeLessThanOrEqual(320);
});
test('a delayed response uses the current language and a visible notice retranslates', async ({
  page,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route('**/api/v2/projects/refresh', async (route) => {
    await gate;
    await route.fulfill({
      status: 409,
      json: mockEnvelope(
        route.request().url(),
        {
          detail: 'Not enough server storage',
          messages: { detail: { code: 'errors.notEnoughServerStorage', params: {} } },
        },
        409,
      ),
    });
  });
  try {
    await page.goto('/');
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await page.locator('#menu-toggle').click();
    await page.locator('#refresh').click();
    await page.locator('#language-toggle').click();
    await page.locator('#language-zh').click();
    release();
    await expect(page.locator('#message')).toHaveText('服务器空间不足');
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await expect(page.locator('#message')).toHaveText('Not enough server storage');
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});
test('unknown server errors do not display raw diagnostic strings in English', async ({ page }) => {
  await page.route('**/api/v2/projects/refresh', (route) =>
    route.fulfill({
      status: 500,
      json: mockEnvelope(
        route.request().url(),
        {
          detail: '内部错误 password=SECRET',
          messages: { detail: { code: 'constructor', params: {} } },
        },
        500,
      ),
    }),
  );
  await page.goto('/');
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await page.locator('#menu-toggle').click();
  await page.locator('#refresh').click();
  await expect(page.locator('#message')).toHaveText('Request failed. Please retry.');
  await expect(page.locator('body')).not.toContainText('SECRET');
});
test('login language updates preserve credentials and localize required-field errors', async ({
  page,
}) => {
  await page.goto('/login');
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await page.locator('#login-submit').click();
  await expect(page.locator('#login-error')).toHaveText('Enter your username');
  await page.locator('#username').fill('keep-user');
  await page.locator('#password').fill('keep-password');
  await page.locator('#language-toggle').click();
  await page.locator('#language-zh').click();
  await expect(page.locator('#username')).toHaveValue('keep-user');
  await expect(page.locator('#password')).toHaveValue('keep-password');
  await expect(page.locator('#login-error')).toHaveText('请输入账号');
});
test('server browser empty state and progress accessibility labels update in place', async ({
  page,
}) => {
  await page.route('**/api/v2/imports/sources', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { roots: [], entries: [] }, 200) }),
  );
  await page.goto('/#add');
  await page.getByRole('radio', { name: '服务器文件', exact: true }).locator('..').click();
  await expect(page.locator('#server-source')).toContainText('没有可用的服务器目录');
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await expect(page.locator('#server-source')).toContainText('No server folders available');
  await expect(page.locator('#server-source .ant-select')).toContainText('Choose location');
  await expect(page.locator('#upload-progress .ant-progress')).toHaveAttribute(
    'aria-label',
    'Uploading',
  );
});
test('completed import warning retains its identity across language changes', async ({ page }) => {
  const game = renpyProjectFixture({
    project_id: 'f'.repeat(32),
    name: 'Warning fixture',
    path: '/projects/fixture',
    release_version: '1.0',
    size_bytes: 1,
  });
  let completed = false;
  await page.route('**/api/v2/projects', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) }),
  );
  await page.route('**/api/v2/projects/' + game.project_id, (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), game, 200) }),
  );
  const task = () => ({
    task_id: 'warning-task',
    state: completed ? 'completed' : 'running',
    phase: completed ? 'completed' : 'cleaning',
    completed_bytes: 0,
    total_bytes: null,
    error: null,
    warning: completed ? 'The game was registered, but source cleanup failed.' : null,
    messages: completed
      ? { warning: { code: 'errors.theGameWasRegisteredButSourceCleanupFailed', params: {} } }
      : {},
    project: completed ? game : null,
    source_name: 'fixture.zip',
    upload_id: null,
    directory_upload_id: null,
    delete_source: false,
    abandoned: false,
  });
  await page.route('**/api/v2/imports/active', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), task(), 200) }),
  );
  await page.route('**/api/v2/tasks/imports/warning-task', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), task(), 200) }),
  );
  await page.goto('/#add');
  await expect(page.locator('#processing-detail')).toContainText('正在清理');
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  completed = true;
  await expect(page.locator('#message')).toContainText('source cleanup failed');
  await page.locator('#language-toggle').click();
  await page.locator('#language-zh').click();
  await expect(page.locator('#message')).toHaveText('游戏已登记，但来源文件清理失败');
});
test('cross-tab language changes preserve focused engine selection and localize its labels', async ({
  page,
}) => {
  const game = renpyProjectFixture({
    project_id: 'e'.repeat(32),
    name: 'Selection fixture',
    path: '/projects/fixture',
    release_version: null,
    size_bytes: 1,
    play_seconds: 61,
    runtime_override: 'renpy:8.2.3',
    runtime_options: [runtimeFixture('8.3.7'), runtimeFixture('8.2.3')],
  });
  await page.route('**/api/v2/projects', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) }),
  );
  await page.route('**/api/v2/projects/' + game.project_id, (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), game, 200) }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { state: 'stopped' }, 200) }),
  );
  await page.goto('/#projects/' + game.project_id);
  const select = page.locator('#details .ant-select');
  await expect(select.locator('.vn-select-value')).toHaveText('8.2.3');
  const focused = select.getByRole('combobox');
  await focused.focus();
  await expect(focused).toBeFocused();
  await page.evaluate(() => {
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        language: 'en',
      }),
    );
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'vn-stream:preferences',
        newValue: JSON.stringify({
          ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
          language: 'en',
        }),
      }),
    );
  });
  await expect(focused).toBeFocused();
  await expect(select.locator('.vn-select-value')).toHaveText('8.2.3');
  await expect(focused).toHaveAttribute('aria-label', 'Engine version');
  await focused.click();
  await expect(
    page.getByRole('option', { name: '8.3.7 (Recommended)', exact: true }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#details .delete-game svg')).toHaveCount(1);
  await page.locator('#details .delete-game').click();
  await expect(page.locator('#delete-dialog')).toBeVisible();
  await page.evaluate(() => {
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        language: 'zh',
      }),
    );
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'vn-stream:preferences',
        newValue: JSON.stringify({
          ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
          language: 'zh',
        }),
      }),
    );
  });
  await expect(page.locator('#delete-dialog')).toBeVisible();
  await expect(page.getByRole('dialog')).toContainText('永久删除');
});
test('deletion failure descriptors translate while an engine selection keeps focus', async ({
  page,
}) => {
  const game = renpyProjectFixture({
    project_id: 'd'.repeat(32),
    name: 'Deletion message fixture',
    path: '/projects/self-authored',
    release_version: '1',
    size_bytes: 1,
    deletion: {
      task_id: 'own-test',
      project_id: 'd'.repeat(32),
      phase: 'prepared',
      name: 'Deletion message fixture',
      state: 'failed',
      error: '无法删除项目，请检查服务器日志。',
      messages: { error: { code: 'game.deleteFailed', params: {} } },
    },
  });
  await page.route('**/api/v2/projects', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) }),
  );
  await page.route('**/api/v2/projects/' + game.project_id, (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), game, 200) }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { state: 'stopped' }, 200) }),
  );
  await page.goto('/#projects/' + game.project_id);
  const focused = page.locator('#details').getByRole('combobox');
  await focused.focus();
  await page.evaluate(() =>
    window.dispatchEvent(
      new StorageEvent('storage', {
        key: 'vn-stream:preferences',
        newValue: JSON.stringify({
          ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
          language: 'en',
        }),
      }),
    ),
  );
  await expect(focused).toBeFocused();
  await expect(page.locator('.deletion-error')).toHaveText(
    'Unable to delete the project. Check server logs.',
  );
});
