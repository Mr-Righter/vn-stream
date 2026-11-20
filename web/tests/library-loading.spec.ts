import { mockEnvelope, apiData, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
const game = renpyProjectFixture({
  project_id: 'd'.repeat(32),
  name: 'Loading fixture',
  path: '/projects/fixture',
  release_version: '1.0',
  size_bytes: 1024,
  runtime_build_version: '8.3.7',
});
test('pending catalog is loading rather than an empty library', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v2/projects', async (route) => {
    await gate;
    await route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) });
  });
  try {
    await page.goto('/');
    await expect(page.locator('#games')).toContainText('正在加载游戏库', { timeout: 1500 });
    await expect(page.locator('#games')).not.toContainText('游戏库还是空的');
    release();
    await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});
test('a running game remains resumable after its runtime becomes unavailable', async ({ page }) => {
  await page.route('**/api/v2/projects', (route) =>
    route.fulfill({
      json: mockEnvelope(route.request().url(), [{ ...game, runtime_options: [] }], 200),
    }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          state: 'running',
          project_id: game.project_id,
          session_id: 'c'.repeat(32),
        },
        200,
      ),
    }),
  );
  await page.goto('/');
  await expect(page.locator('#active button')).toContainText('继续', { timeout: 1500 });
  await page.getByRole('button', { name: game.name, exact: true }).click();
  await expect(page.getByRole('button', { name: '继续', exact: true })).toBeEnabled();
});
test('starting has feedback and prevents repeated starts', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let starts = 0;
  await page.route(`**/api/v2/projects/${game.project_id}`, (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), game, 200) }),
  );
  await page.route('**/api/v2/projects', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { state: 'stopped' }, 200) }),
  );
  await page.route(`**/api/v2/projects/${game.project_id}/start`, async (route) => {
    starts++;
    await gate;
    await route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { state: 'running', project_id: game.project_id, session_id: 'c'.repeat(32) },
        200,
      ),
    });
  });
  await page.route('**/api/v2/stream', (route) =>
    route.fulfill({
      json: mockEnvelope(route.request().url(), { available: false, url: null }, 200),
    }),
  );
  try {
    await page.goto(`/#projects/${game.project_id}`);
    await page.getByRole('button', { name: '开始', exact: true }).click();
    await expect(page.getByRole('button', { name: '正在启动…', exact: true })).toBeDisabled({
      timeout: 1500,
    });
    await expect.poll(() => starts).toBe(1);
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});
test('failed initial catalog offers retry without claiming the library is empty', async ({
  page,
}) => {
  let failed = true;
  await page.route('**/api/v2/projects', (route) =>
    failed
      ? route.fulfill({
          status: 503,
          json: mockEnvelope(route.request().url(), { detail: 'Temporarily unavailable' }, 503),
        })
      : route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) }),
  );
  await page.goto('/');
  await expect(page.locator('#games')).toContainText('暂时无法加载游戏库');
  await expect(page.locator('#games')).not.toContainText('游戏库还是空的');
  failed = false;
  await page.locator('#games').getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
});
test('catalog refreshes coalesce while session status keeps updating', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let catalogs = 0;
  let running = false;
  await page.route('**/api/v2/projects', async (route) => {
    catalogs++;
    if (catalogs > 1) await gate;
    await route.fulfill({ json: mockEnvelope(route.request().url(), [game], 200) });
  });
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        running
          ? { state: 'running', project_id: game.project_id, session_id: 'c'.repeat(32) }
          : { state: 'stopped' },
        200,
      ),
    }),
  );
  try {
    await page.clock.install();
    await page.goto('/');
    await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
    await page.clock.fastForward(5000);
    await expect.poll(() => catalogs).toBe(2);
    running = true;
    // A virtual timer leap can land while the preceding HTTP response is still
    // resolving. Advance through subsequent poll ticks while real network I/O settles.
    await expect
      .poll(async () => {
        await page.clock.runFor(5000);
        return (await page.locator('#active button').allTextContents()).join(' ');
      })
      .toContain('继续');
    await expect(page.getByRole('button', { name: game.name, exact: true })).toBeVisible();
    expect(catalogs).toBe(2);
    release();
    // No timer tick after the pending request: there must be no queued catalog.
    await page.waitForTimeout(200);
    expect(catalogs).toBe(2);
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});
