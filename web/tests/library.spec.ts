import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
let serial = 0;
async function project(request: import('@playwright/test').APIRequestContext, version = '8.3.7') {
  const name = `Library-${Date.now()}-${serial++}`;
  const root = resolve('../var/e2e/inbox', name);
  await mkdir(`${root}/game`, { recursive: true });
  await mkdir(`${root}/renpy`);
  await writeFile(`${root}/game/script.rpyc`, 'fixture');
  await writeFile(
    `${root}/renpy/__init__.py`,
    `version_tuple = (${version.replaceAll('.', ',')},0)`,
  );
  const sdk = resolve('../var/e2e/data/runtimes/renpy/8.3.7');
  await mkdir(`${sdk}/renpy`, { recursive: true });
  await writeFile(`${sdk}/renpy/__init__.py`, 'version_tuple = (8,3,7,0)');
  await writeFile(`${sdk}/renpy.sh`, 'sleep 30');
  const response = await request.post('/api/v2/projects', { data: { path: root } });
  expect(response.status(), await response.text()).toBe(201);
  return response.json().then(apiData);
}
test('recommended automatic choice survives refresh', async ({ page, request }) => {
  const game = await project(request);
  await page.goto('/');
  await page.getByRole('button', { name: game.name, exact: true }).click();
  await page.getByRole('combobox', { name: '引擎版本' }).click();
  await page.getByRole('option', { name: '8.3.7（推荐）', exact: true }).click();
  await page.getByRole('button', { name: '管理菜单' }).click();
  const refreshed = page.waitForResponse(
    (response) => new URL(response.url()).pathname === '/api/v2/projects/refresh',
  );
  await page.locator('#refresh').click();
  expect((await refreshed).ok()).toBe(true);
  await expect(page.locator('#details .vn-select-value')).toContainText('推荐');
});
test('one password login needs no token form', async ({ page, playwright }) => {
  await page.goto('/');
  await expect(page.locator('h1')).toHaveText('我的游戏');
  await expect(page.locator('input[type=password]:visible')).toHaveCount(0);
  const wrong = await playwright.request.newContext({
    httpCredentials: { username: 'wrong', password: 'wrong' },
  });
  try {
    expect((await wrong.get('http://127.0.0.1:18081/api/v2/session')).status()).toBe(401);
  } finally {
    await wrong.dispose();
  }
});
test('start uses automatic recommendation and navigates in same tab', async ({
  page,
  request,
  context,
}) => {
  const game = await project(request, '8.3.6');
  try {
    await page.goto(`/#projects/${game.project_id}`);
    await expect(page.locator('#details .vn-select-value')).toContainText('推荐');
    await page.getByRole('button', { name: '开始', exact: true }).click();
    await expect(page).toHaveURL(/\?session=[a-f0-9]{32}/);
    expect(context.pages()).toHaveLength(1);
    const status = apiData(await (await request.get('/api/v2/session')).json());
    expect(status.state).toBe('running');
    expect(
      apiData(await (await request.get(`/api/v2/projects/${game.project_id}`)).json())
        .runtime_override,
    ).toBeNull();
  } finally {
    await request.post('/api/v2/session/stop');
  }
});
test('menu omits global engine management and remains anchored', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#catalog .game-row').first()).toBeVisible();
  const before = await page.locator('#catalog').boundingBox();
  await page.getByRole('button', { name: '管理菜单' }).click();
  await expect(page.locator('#add-game')).toBeVisible();
  expect(await page.locator('#catalog').boundingBox()).toEqual(before);
  await expect(page.locator('#engines')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: '管理菜单' })).toBeFocused();
});
test('recovering session disables starting', async ({ page, request }) => {
  const game = await project(request);
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { state: 'recovering', reason: 'supervisor_lost' },
        200,
      ),
    }),
  );
  await page.goto(`/#projects/${game.project_id}`);
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeDisabled();
});
test('recent list and reconnect preserve process', async ({ page, request, context }) => {
  const game = await project(request);
  try {
    const started = apiData(
      await (await request.post(`/api/v2/projects/${game.project_id}/start`, { data: {} })).json(),
    );
    await page.goto('/');
    await page.getByRole('radio', { name: '最近玩过', exact: true }).locator('..').click();
    await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
    await page.close();
    const again = await context.newPage();
    await again.goto(`/#projects/${game.project_id}`);
    await expect(again.getByRole('button', { name: '继续', exact: true })).toBeVisible();
    const current = apiData(await (await request.get('/api/v2/session')).json());
    expect(current.pid).toBe(started.pid);
    expect(current.session_id).toBe(started.session_id);
  } finally {
    await request.post('/api/v2/session/stop');
  }
});
test('phone details keep start button in viewport', async ({ page, request }) => {
  const game = await project(request);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/#projects/${game.project_id}`);
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeInViewport();
  await page.setViewportSize({ width: 844, height: 300 });
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeInViewport();
});
test('import waits for an older in-flight refresh before opening new details', async ({ page }) => {
  const root = resolve('../var/e2e/inbox', `Race-${Date.now()}`);
  await mkdir(`${root}/game`, { recursive: true });
  await writeFile(`${root}/game/script.rpyc`, 'fixture');
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let first = true;
  await page.route('**/api/v2/projects', async (route) => {
    const response = await route.fetch();
    if (first) {
      first = false;
      await gate;
    }
    await route.fulfill({ response });
  });
  try {
    await page.goto('/');
    await page.getByRole('button', { name: '管理菜单' }).click();
    await page.locator('#add-game').click();
    await page.getByRole('radio', { name: '服务器文件', exact: true }).locator('..').click();
    await page.locator('#source-roots').click();
    await page.getByRole('option').first().click();
    await page.getByRole('button', { name: `${root.split('/').pop()}`, exact: true }).click();
    const imported = page.waitForResponse((response) => response.url().endsWith('/api/v2/imports'));
    await page.getByRole('button', { name: '导入当前游戏目录', exact: true }).click();
    await page.getByRole('button', { name: '导入', exact: true }).click();
    await imported;
    // Optimistic details can appear before the fresh catalog returns. Wait for
    // that catalog too, so the assertion also catches a stale refresh overwrite.
    const freshCatalog = page.waitForResponse(
      async (response) =>
        response.url().endsWith('/api/v2/projects') &&
        apiData(await response.json()).some(
          (game: { name: string }) => game.name === root.split('/').pop(),
        ),
    );
    release();
    await freshCatalog;
    await expect(page.locator('#details h2')).toHaveText(root.split('/').pop()!);
  } finally {
    release();
    // Drain route.fetch/fulfill while the page and context are still alive.
    await page.unrouteAll({ behavior: 'wait' });
  }
});
