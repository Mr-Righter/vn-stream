import { mockEnvelope, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect, setAppearance, type Page } from './fixtures.js';
const target = 'e'.repeat(32),
  source = 'a'.repeat(32);
const metadata = (id: string, name: string) => ({
  project_id: id,
  name,
  display_name: name,
  engine_version: '8.3.2',
  release_version: '1.2',
});
const entry = (name: string, valid = true, action = 'overwrite') => ({
  name,
  kind: name === 'persistent' ? 'persistent' : 'manual',
  size_bytes: 120,
  updated_at: new Date(1 * 1000).toISOString(),
  source_copies: 1,
  target_copies: 1,
  action,
  valid,
  reason: valid ? null : { code: 'saves.unsupported', params: {} },
});
const preview = {
  id: 'b'.repeat(32),
  kind: 'migration',
  source: metadata(source, 'Old release'),
  target: metadata(target, 'New release'),
  include_persistent: true,
  created_at: new Date(1 * 1000).toISOString(),
  entries: [entry('1-1.save'), entry('persistent'), entry('broken.save', false)],
  counts: { total: 3, valid: 2, invalid: 1, add: 0, overwrite: 3 },
  required_bytes: 640,
  warnings: [],
};
const task = (state = 'running', kind = 'migration') => ({
  task_id: kind === 'undo' ? 'undo-task' : 'task',
  kind,
  source_id: source,
  target_id: target,
  source: preview.source,
  target: preview.target,
  preview_id: preview.id,
  request_id: 'request',
  skip_invalid: true,
  state,
  phase: state === 'completed' ? 'completed' : 'writing',
  created_at: new Date(1 * 1000).toISOString(),
  updated_at: new Date(1 * 1000).toISOString(),
  total: 2,
  prepared: 2,
  backed_up: 2,
  completed: state === 'completed' ? 2 : 1,
  skipped: 1,
  results: [
    {
      name: '1-1.save',
      action: 'overwrite',
      state: state === 'completed' ? 'completed' : 'pending',
      reason: null,
    },
  ],
  error: null,
  migration_id: kind === 'undo' ? 'task' : null,
  undo_id: null,
});
async function prepare(
  page: Page,
  options: {
    failCommit?: boolean;
    allInvalid?: boolean;
    running?: boolean;
  } = {},
) {
  await page.addInitScript(() =>
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        language: 'en',
      }),
    ),
  );
  const submissions: Record<string, unknown>[] = [],
    previews: Record<string, unknown>[] = [];
  let reads = 0;
  await page.route('**/api/v2/**', async (route) => {
    const path = new URL(route.request().url()).pathname.split('/api/v2/')[1];
    let data: unknown = null;
    if (path === 'projects')
      data = [
        renpyProjectFixture({
          ...metadata(target, 'New release'),
          path: '/projects/new',
          size_bytes: 1,
          recommendation: { kind: 'exact', build_version: '8.3.2', runtime_id: 'renpy:8.3.2' },
        }),
      ];
    if (path === 'engines') data = engineFixtures;
    if (path === 'session') data = { state: 'stopped' };
    if (path === `projects/${target}/saves`)
      data = {
        writable: true,
        saves: [
          {
            id: 'slot',
            name: '1-1.save',
            metadata: {},
            kind: 'manual',
            updated_at: new Date(1 * 1000).toISOString(),
            size_bytes: 120,
            copies: 1,
          },
        ],
      };
    if (path === `projects/${target}/saves/slot`)
      data = {
        id: 'slot',
        name: '1-1.save',
        metadata: {},
        kind: 'manual',
        updated_at: new Date(1 * 1000).toISOString(),
        size_bytes: 120,
        copies: 1,
        writable: true,
      };
    if (path === `projects/${target}/saves/slot/variables`)
      data = {
        revision: 'd'.repeat(64),
        editable: true,
        writable: true,
        fields: [{ id: 'flag', name: 'store.flag', type: 'boolean', value: false }],
        total: 1,
        counts: { boolean: 1 },
      };
    if (path === `projects/${target}/saves/migration-sources`)
      data = [
        {
          ...metadata(source, 'Old release'),
          counts: { manual: 1, auto: 0, quick: 0, persistent: 1, total: 3 },
          available: true,
        },
      ];
    if (path === `projects/${target}/saves/migrations/preview`) {
      previews.push(route.request().postDataJSON());
      data = options.allInvalid
        ? {
            ...preview,
            entries: [entry('broken.save', false)],
            counts: { total: 1, valid: 0, invalid: 1, add: 0, overwrite: 1 },
          }
        : preview;
    }
    if (path === `projects/${target}/saves/migrations`) {
      if (route.request().method() === 'POST') {
        submissions.push(route.request().postDataJSON());
        if (options.failCommit && submissions.length === 1)
          return route.fulfill({
            status: 503,
            json: mockEnvelope(route.request().url(), { detail: 'SECRET_INTERNAL_FAILURE' }, 503),
          });
        data = task();
      } else data = [task('completed')];
    }
    if (path === 'tasks/save-migrations/task') {
      reads++;
      data = task(options.running || reads === 1 ? 'running' : 'completed');
    }
    if (path === 'tasks/save-migrations/task/undo-preview')
      data = {
        ...preview,
        id: 'f'.repeat(32),
        kind: 'undo',
        migration_id: 'task',
        entries: [entry('1-1.save'), entry('persistent', true, 'remove')],
        counts: { total: 2, valid: 2, invalid: 0, add: 0, overwrite: 1 },
      };
    if (path === 'tasks/save-migrations/task/undo') {
      submissions.push(route.request().postDataJSON());
      data = task('completed', 'undo');
    }
    if (path === 'tasks/save-migrations/undo-task') data = task('completed', 'undo');
    await route.fulfill({ json: mockEnvelope(route.request().url(), data, 200) });
  });
  await page.goto(`/#projects/${target}/saves`);
  return { submissions, previews };
}
async function selectSource(page: Page) {
  await page.getByRole('searchbox', { name: 'Search source projects', exact: true }).fill('Old');
  await page.getByRole('combobox', { name: 'Source project', exact: true }).click();
  await expect(page.locator('.vn-migration-source-popup:visible')).toContainText(
    "Release 1.2 · Ren'Py 8.3.2",
  );
  await page.getByText('Old release', { exact: true }).last().click();
}
async function openPreview(page: Page) {
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  await selectSource(page);
  await page.getByRole('button', { name: 'Preview migration', exact: true }).click();
}
test.describe('touch source selection', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  test('opening migration and choosing a source do not focus an editable input', async ({
    page,
  }) => {
    await prepare(page);
    await page.getByRole('button', { name: 'Migrate saves', exact: true }).tap();
    const editableFocused = () =>
      page.evaluate(() => {
        const element = document.activeElement;
        return element instanceof HTMLInputElement && !element.readOnly;
      });
    expect(await editableFocused()).toBe(false);
    const selector = page.getByRole('combobox', { name: 'Source project', exact: true });
    await selector.tap();
    await expect(page.getByRole('option').filter({ hasText: 'Old release' })).toBeVisible();
    expect(await editableFocused()).toBe(false);
    await page.getByRole('option').filter({ hasText: 'Old release' }).tap();
    await expect(page.locator('.vn-migration-source-meta')).toContainText('Old release');
    expect(await editableFocused()).toBe(false);
  });
  test('search is explicit and filters sources without changing the selected project', async ({
    page,
  }) => {
    await prepare(page);
    await page.route(`**/api/v2/projects/${target}/saves/migration-sources`, (route) =>
      route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          [
            { ...metadata(source, 'Old release'), counts: { total: 3 }, available: true },
            { ...metadata('c'.repeat(32), 'Other release'), counts: { total: 2 }, available: true },
          ],
          200,
        ),
      }),
    );
    await page.getByRole('button', { name: 'Migrate saves', exact: true }).tap();
    const search = page.getByRole('searchbox', { name: 'Search source projects', exact: true });
    await expect(search).not.toBeFocused();
    await search.tap();
    await expect(search).toBeFocused();
    await search.fill('Other');
    const selector = page.getByRole('combobox', { name: 'Source project', exact: true });
    await selector.tap();
    await expect(selector).toBeFocused();
    await expect(selector).not.toBeEditable();
    await expect(page.getByRole('option').filter({ hasText: 'Old release' })).toHaveCount(0);
    await page.getByRole('option').filter({ hasText: 'Other release' }).tap();
    await expect(page.locator('.vn-migration-source-meta')).toContainText('Other release');
    await search.fill('Old');
    await expect(page.locator('.vn-migration-source-meta')).toContainText('Other release');
    await search.clear();
    await selector.tap();
    await expect(page.getByRole('option')).toHaveCount(2);
  });
});
test('source search and picker follow the form keyboard order', async ({ page }) => {
  await prepare(page);
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  const search = page.getByRole('searchbox', { name: 'Search source projects', exact: true });
  await search.focus();
  await search.fill('Old');
  await page.keyboard.press('Tab');
  const selector = page.getByRole('combobox', { name: 'Source project', exact: true });
  await expect(selector).toBeFocused();
  await selector.press('ArrowDown');
  await expect(search).not.toBeFocused();
  await selector.press('Enter');
  await expect(page.locator('.vn-migration-source-meta')).toContainText('Old release');
  await expect(page.locator('.vn-migration-source-popup:visible')).toHaveCount(0);
  await expect(selector).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('checkbox', { name: 'Include persistent data', exact: true }),
  ).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(selector).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(search).toBeFocused();
});
test('source choice, default persistent, explicit invalid skip and idempotent retry', async ({
  page,
}) => {
  const api = await prepare(page, { failCommit: true });
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  await expect(page).toHaveURL(/\/saves\/migrate$/);
  await expect(page.locator('.hero')).toBeHidden();
  await expect(page.locator('.vn-details-tabs')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Preview migration', exact: true })).toBeDisabled();
  await expect(
    page.getByRole('checkbox', { name: 'Include persistent data', exact: true }),
  ).toBeChecked();
  await selectSource(page);
  await expect(page.locator('.vn-migration-source-meta')).toContainText('1.2');
  await expect(page.locator('.vn-migration-source-meta')).toContainText('8.3.2');
  await page.getByRole('button', { name: 'Preview migration', exact: true }).click();
  expect(api.previews).toEqual([{ source_id: source, include_persistent: true }]);
  await expect(page.locator('.vn-migration-page')).toContainText('Selected save data: 240 B');
  await expect(page.locator('.vn-migration-page')).toContainText(
    'Temporary files and backups require approximately 640 B',
  );
  const submit = page.getByRole('button', { name: 'Start migration', exact: true });
  await expect(submit).toBeDisabled();
  await page.getByRole('button', { name: 'File details', exact: true }).click();
  await expect(page.getByText('broken.save', { exact: true })).toBeVisible();
  await page
    .getByRole('checkbox', { name: 'Skip invalid files and migrate valid files only', exact: true })
    .check();
  await submit.click();
  await expect(page.locator('.vn-migration-page [role="alert"]').first()).toBeVisible();
  await expect(page.locator('.vn-migration-page')).not.toContainText('SECRET_INTERNAL_FAILURE');
  await submit.click();
  await expect(page).toHaveURL(/\?task=task$/);
  expect(api.submissions).toHaveLength(2);
  expect(api.submissions[1]).toEqual(api.submissions[0]);
  expect(api.submissions[0].skip_invalid).toBe(true);
  await expect(page.getByTestId('migration-task')).toContainText('50%');
  await expect(page.getByTestId('migration-task')).toContainText('100%', { timeout: 10000 });
});
test('task URL restores without enqueueing and history undo needs confirmation', async ({
  page,
}) => {
  const api = await prepare(page);
  await page.goto(`/#projects/${target}/saves/migrate?task=task`);
  await expect(page.getByTestId('migration-task')).toContainText('100%', { timeout: 10000 });
  await page.reload();
  await expect(page.getByTestId('migration-task')).toContainText('100%');
  expect(api.submissions).toHaveLength(0);
  await page
    .locator('.vn-migration-history')
    .getByRole('button', { name: 'Undo migration', exact: true })
    .click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('1-1.save');
  await expect(dialog).toContainText('persistent');
  await expect(dialog).toContainText('Remove');
  expect(api.submissions).toHaveLength(0);
  await dialog.getByRole('button', { name: 'Confirm undo', exact: true }).click();
  await expect(page).toHaveURL(/task=undo-task$/);
  expect(api.submissions).toHaveLength(1);
});
test('all-invalid preview cannot commit even with skip confirmation', async ({ page }) => {
  await prepare(page, { allInvalid: true });
  await openPreview(page);
  await page
    .getByRole('checkbox', { name: 'Skip invalid files and migrate valid files only', exact: true })
    .check();
  await expect(page.getByRole('button', { name: 'Start migration', exact: true })).toBeDisabled();
});
test('preferences preserve source and active progress', async ({ page }) => {
  await prepare(page, { running: true });
  await openPreview(page);
  await page.locator('#language-toggle').click();
  await page.locator('#language-zh').click();
  await page.locator('#theme-toggle').click();
  await expect(page.locator('.vn-migration-source-meta')).toContainText('Old release');
  await page.goto(`/#projects/${target}/saves/migrate?task=task`);
  await expect(page.getByTestId('migration-task')).toContainText('50%');
  await page.locator('#theme-toggle').click();
  await expect(page.getByTestId('migration-task')).toContainText('50%');
});
for (const width of [320, 390, 844, 1280])
  test(`migration fits ${width}px in both languages and themes at 200% text`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
    await prepare(page);
    await openPreview(page);
    await page.getByRole('button', { name: 'File details', exact: true }).click();
    for (const language of ['en', 'zh']) {
      if (language === 'zh') {
        await page.locator('#language-toggle').click();
        await page.locator('#language-zh').click();
      }
      for (const appearance of ['dark', 'light']) {
        await setAppearance(page, appearance);
        for (const zoom of ['1', '2']) {
          await page.locator('body').evaluate((body, value) => (body.style.zoom = value), zoom);
          await expect
            .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
            .toBe(true);
          const geometry = await page
            .locator('.vn-migration-page .ant-btn:visible')
            .evaluateAll((buttons) =>
              buttons.map((button) => {
                const rect = button.getBoundingClientRect();
                const tops: number[] = [];
                const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                  if (!walker.currentNode.textContent?.trim()) continue;
                  const range = document.createRange();
                  range.selectNodeContents(walker.currentNode);
                  tops.push(...[...range.getClientRects()].map((rect) => Math.round(rect.top)));
                }
                return {
                  width: rect.width,
                  height: rect.height,
                  inside: rect.left >= 0 && rect.right <= innerWidth + 1,
                  lines: new Set(tops).size,
                };
              }),
            );
          for (const button of geometry) {
            expect(button.height).toBeGreaterThanOrEqual(44 * Number(zoom));
            expect(button.width).toBeGreaterThanOrEqual(44 * Number(zoom));
            expect(button.inside).toBe(true);
            expect(button.lines).toBeLessThanOrEqual(1);
          }
        }
        await page.locator('body').evaluate((body) => (body.style.zoom = '1'));
        if (width === 390)
          await page.screenshot({
            path: `../var/save-migration-work/preview-${language}-${appearance}-390.png`,
            fullPage: true,
          });
      }
    }
  });
test('editor draft guard cancels and then allows the migration route', async ({ page }) => {
  await prepare(page);
  await page.goto(`/#projects/${target}/saves/item/slot`);
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await page.evaluate((hash) => {
    location.hash = hash;
  }, `#projects/${target}/saves/migrate`);
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Discard your unsaved changes?');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(/\/saves\/item\/slot$/);
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
  await page.evaluate((hash) => {
    location.hash = hash;
  }, `#projects/${target}/saves/migrate`);
  await dialog.getByRole('button', { name: 'OK', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Source project', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Preview migration', exact: true })).toBeDisabled();
});
test('slow task reads never overlap and stale task responses do not replace the new task', async ({
  page,
}) => {
  const api = await prepare(page);
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  let active = 0,
    maxActive = 0,
    oldReads = 0,
    newReads = 0;
  await page.route('**/api/v2/tasks/save-migrations/task', async (route) => {
    active++;
    maxActive = Math.max(active, maxActive);
    oldReads++;
    await ready;
    active--;
    await route.fulfill({ json: mockEnvelope(route.request().url(), task(), 200) });
  });
  await page.route('**/api/v2/tasks/save-migrations/undo-task', async (route) => {
    active++;
    maxActive = Math.max(active, maxActive);
    newReads++;
    await route.fulfill({
      json: mockEnvelope(route.request().url(), task('completed', 'undo'), 200),
    });
    active--;
  });
  await page.goto(`/#projects/${target}/saves/migrate?task=task`);
  await expect.poll(() => oldReads).toBe(1);
  await page.evaluate((hash) => {
    location.hash = hash;
  }, `#projects/${target}/saves/migrate?task=undo-task`);
  await page.locator('#theme-toggle').click();
  expect(newReads).toBe(0);
  release();
  await expect(page.getByTestId('migration-task')).toContainText('Migration undo');
  await expect(page.getByTestId('migration-task')).toContainText('100%');
  expect(maxActive).toBe(1);
  expect(oldReads).toBe(1);
  expect(api.submissions).toHaveLength(0);
});
test('leaving during preview never applies a stale preview to another source', async ({ page }) => {
  await prepare(page);
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/v2/projects/${target}/saves/migration-sources`, (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        [
          { ...metadata(source, 'Old release'), counts: { total: 3 }, available: true },
          { ...metadata('c'.repeat(32), 'Other release'), counts: { total: 2 }, available: true },
        ],
        200,
      ),
    }),
  );
  let requested = false;
  await page.route(`**/api/v2/projects/${target}/saves/migrations/preview`, async (route) => {
    requested = true;
    await ready;
    await route.fulfill({ json: mockEnvelope(route.request().url(), preview, 200) });
  });
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  await selectSource(page);
  await page.getByRole('button', { name: 'Preview migration', exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  await page.evaluate((hash) => {
    location.hash = hash;
  }, `#projects/${target}/saves`);
  release();
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  const selector = page.getByRole('combobox', { name: 'Source project', exact: true });
  await page.getByRole('searchbox', { name: 'Search source projects', exact: true }).fill('Other');
  await selector.click();
  await expect(page.getByRole('option').filter({ hasText: 'Old release' })).toHaveCount(0);
  await page.getByRole('option').filter({ hasText: 'Other release' }).click();
  await expect(page.locator('.vn-migration-source-meta')).toContainText('Other release');
  await expect(page.getByRole('button', { name: 'Start migration', exact: true })).toHaveCount(0);
});
for (const state of ['loading', 'empty', 'error'])
  test(`source choices handle ${state} safely`, async ({ page }) => {
    await prepare(page);
    let release!: () => void;
    const ready = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/v2/projects/${target}/saves/migration-sources`, async (route) => {
      if (state === 'loading') await ready;
      await route.fulfill(
        state === 'error'
          ? { status: 500, json: { detail: 'SECRET_EXCEPTION' } }
          : { json: mockEnvelope(route.request().url(), []) },
      );
    });
    await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
    const region = page.locator('.vn-migration-page');
    if (state === 'loading') {
      await expect(region.locator('.ant-skeleton').first()).toBeVisible();
      release();
    }
    if (state === 'empty' || state === 'loading')
      await expect(region.getByText('No source projects available', { exact: true })).toBeVisible();
    if (state === 'error') {
      await expect(region.getByRole('alert').first()).toBeVisible();
      await expect(region).not.toContainText('SECRET_EXCEPTION');
      await expect(region.getByRole('button', { name: 'Retry', exact: true })).toBeVisible();
    }
    await expect(
      page.getByRole('button', { name: 'Preview migration', exact: true }),
    ).toBeDisabled();
  });
test('failed task reasons and per-file outcomes follow the current language safely', async ({
  page,
}) => {
  await prepare(page);
  await page.route('**/api/v2/tasks/save-migrations/task', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          ...task('failed'),
          phase: 'failed',
          error: 'SECRET_INTERNAL_FAILURE',
          messages: { error: { code: 'migration.changed', params: {} } },
          results: ['not_written', 'rolled_back', 'recovering'].map((state) => ({
            name: `${state}.save`,
            action: 'overwrite',
            state,
            reason: null,
          })),
        },
        200,
      ),
    }),
  );
  await page.goto(`/#projects/${target}/saves/migrate?task=task`);
  const result = page.getByTestId('migration-task');
  await expect(result).toContainText(
    'Source or target saves or save locations changed. Preview again.',
  );
  await expect(result).not.toContainText('SECRET_INTERNAL_FAILURE');
  await result.getByRole('button', { name: 'File results', exact: true }).click();
  for (const label of ['Not written', 'Rolled back', 'Recovering'])
    await expect(result.getByText(label, { exact: true })).toBeVisible();
  await page.locator('#language-toggle').click();
  await page.locator('#language-zh').click();
  await expect(result).toContainText('not_written.save');
  await expect(result).not.toContainText(
    'Source or target saves or save locations changed. Preview again.',
  );
  await expect(result).not.toContainText('SECRET_INTERNAL_FAILURE');
});
test('task results and undo confirmation fit a small screen at 200% zoom', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await prepare(page);
  await page.goto(`/#projects/${target}/saves/migrate?task=task`);
  await expect(page.getByTestId('migration-task')).toContainText('100%');
  await page
    .getByTestId('migration-task')
    .getByRole('button', { name: 'File results', exact: true })
    .click();
  await page
    .locator('.vn-migration-history')
    .getByRole('button', { name: 'Undo migration', exact: true })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Confirm undo', exact: true })
    .click({ trial: true });
  for (const zoom of ['1', '2']) {
    await page.locator('body').evaluate((body, value) => (body.style.zoom = value), zoom);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      .toBe(true);
    await expect
      .poll(async () =>
        page
          .getByRole('dialog')
          .getByRole('button')
          .evaluateAll(
            (buttons, scale) =>
              buttons.every((button) => {
                const rect = button.getBoundingClientRect();
                return rect.height >= 44 * scale && rect.width >= 44 * scale;
              }),
            Number(zoom),
          ),
      )
      .toBe(true);
    const boxes = await page
      .getByRole('dialog')
      .getByRole('button')
      .evaluateAll((buttons) =>
        buttons.map((button) => {
          const rect = button.getBoundingClientRect();
          return {
            height: rect.height,
            width: rect.width,
            left: rect.left,
            right: rect.right,
            viewport: innerWidth,
          };
        }),
      );
    await expect
      .poll(() =>
        page
          .getByRole('dialog')
          .locator('.ant-alert,.ant-alert-content,.vn-migration-files')
          .evaluateAll((nodes) =>
            nodes.every((node) => {
              const rect = node.getBoundingClientRect();
              return rect.left >= 0 && rect.right <= innerWidth + 1;
            }),
          ),
      )
      .toBe(true);
    for (const box of boxes) {
      expect(box.height).toBeGreaterThanOrEqual(44 * Number(zoom));
      expect(box.width).toBeGreaterThanOrEqual(44 * Number(zoom));
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(box.viewport);
    }
  }
  await page.screenshot({
    path: '../var/save-migration-work/undo-en-dark-320-zoom.png',
    fullPage: true,
  });
});
test('running history has its own state label', async ({ page }) => {
  await prepare(page);
  await page.route(`**/api/v2/projects/${target}/saves/migrations`, (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), [task('running')], 200) }),
  );
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  await expect(page.locator('.vn-migration-history .ant-tag')).toHaveText('Running');
});
test('migration returns to the filtered save list with its scroll position', async ({ page }) => {
  await prepare(page);
  await page.route(`**/api/v2/projects/${target}/saves`, (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          writable: true,
          saves: Array.from({ length: 50 }, (_, index) => ({
            id: `slot-${index}`,
            name: `slot-${index}.save`,
            kind: 'manual',
            metadata: {},
            updated_at: new Date(1 * 1000).toISOString(),
            size_bytes: 120,
            copies: 1,
          })),
        },
        200,
      ),
    }),
  );
  await page.goto(`/#projects/${target}/saves?search=slot&kind=manual`);
  await expect(page.getByTestId('save-row-slot-49')).toHaveCount(1);
  await page.evaluate(() => scrollTo(0, 400));
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(400);
  await page
    .getByRole('button', { name: 'Migrate saves', exact: true })
    .evaluate((button) => (button as HTMLButtonElement).click());
  await expect(page).toHaveURL(/migrate\?search=slot&kind=manual$/);
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page).toHaveURL(/saves\?search=slot&kind=manual$/);
  await expect(page.getByRole('searchbox')).toHaveValue('slot');
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(400);
});
test('completed migration and undo refresh the parent saves list', async ({ page }) => {
  await prepare(page);
  let migrated = false;
  const slot = {
    id: 'slot',
    name: '1-1.save',
    metadata: {},
    kind: 'manual',
    updated_at: new Date(1 * 1000).toISOString(),
    size_bytes: 120,
    copies: 1,
  };
  await page.route(`**/api/v2/projects/${target}/saves`, (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          writable: true,
          saves: migrated ? [slot, { ...slot, id: 'new-slot', name: 'new.save' }] : [slot],
        },
        200,
      ),
    }),
  );
  await page.route('**/api/v2/tasks/save-migrations/task', (route) => {
    migrated = true;
    return route.fulfill({ json: mockEnvelope(route.request().url(), task('completed'), 200) });
  });
  await page.route('**/api/v2/tasks/save-migrations/task/undo', (route) => {
    migrated = false;
    return route.fulfill({
      json: mockEnvelope(route.request().url(), task('completed', 'undo'), 200),
    });
  });
  await page.reload();
  await expect(page.getByTestId('save-row-slot')).toBeVisible();
  await openPreview(page);
  await page
    .getByRole('checkbox', { name: 'Skip invalid files and migrate valid files only', exact: true })
    .check();
  await page.getByRole('button', { name: 'Start migration', exact: true }).click();
  await expect(page.getByTestId('migration-task')).toContainText('100%');
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page.getByTestId('save-row-new-slot')).toBeVisible();
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  await page
    .locator('.vn-migration-history')
    .getByRole('button', { name: 'Undo migration', exact: true })
    .click();
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm undo', exact: true }).click();
  await expect(page.getByTestId('migration-task')).toContainText('100%');
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  await expect(page.getByTestId('save-row-new-slot')).toHaveCount(0);
  await expect(page.getByTestId('save-row-slot')).toBeVisible();
});
for (const operation of ['preview', 'commit', 'undo-preview'])
  test(`late ${operation} errors cannot appear in a different task`, async ({ page }) => {
    await prepare(page);
    if (operation === 'commit') {
      await openPreview(page);
      await page
        .getByRole('checkbox', {
          name: 'Skip invalid files and migrate valid files only',
          exact: true,
        })
        .check();
    } else {
      await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
      if (operation === 'preview') await selectSource(page);
    }
    let release!: () => void;
    const ready = new Promise<void>((resolve) => {
      release = resolve;
    });
    const path =
      operation === 'preview'
        ? `projects/${target}/saves/migrations/preview`
        : operation === 'commit'
          ? `projects/${target}/saves/migrations`
          : 'tasks/save-migrations/task/undo-preview';
    let requested = false;
    await page.route(`**/api/v2/${path}`, async (route) => {
      if (operation === 'commit' && route.request().method() !== 'POST') return route.fallback();
      requested = true;
      await ready;
      await route.fulfill({
        status: 409,
        json: mockEnvelope(
          route.request().url(),
          { messages: { detail: { code: 'migration.changed', params: {} } } },
          409,
        ),
      });
    });
    if (operation === 'preview')
      await page.getByRole('button', { name: 'Preview migration', exact: true }).click();
    if (operation === 'commit')
      await page.getByRole('button', { name: 'Start migration', exact: true }).click();
    if (operation === 'undo-preview')
      await page
        .locator('.vn-migration-history')
        .getByRole('button', { name: 'Undo migration', exact: true })
        .click();
    await expect.poll(() => requested).toBe(true);
    await page.evaluate((hash) => {
      location.hash = hash;
    }, `#projects/${target}/saves/migrate?task=undo-task`);
    await expect(page.getByTestId('migration-task')).toContainText('Migration undo');
    const response = page.waitForResponse(
      (response) => response.url().endsWith(`/api/v2/${path}`) && response.status() === 409,
    );
    release();
    await response;
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))),
        ),
    );
    await expect(page.locator('.vn-migration-page')).not.toContainText(
      'Source or target saves or save locations changed. Preview again.',
    );
    await expect(page.getByTestId('migration-task')).toContainText('100%');
  });
test('late undo commit errors cannot enter a newer undo confirmation', async ({ page }) => {
  await prepare(page);
  await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
  const openUndo = () =>
    page
      .locator('.vn-migration-history')
      .getByRole('button', { name: 'Undo migration', exact: true })
      .click();
  await openUndo();
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  let requested = false;
  await page.route('**/api/v2/tasks/save-migrations/task/undo', async (route) => {
    requested = true;
    await ready;
    await route.fulfill({
      status: 409,
      json: mockEnvelope(
        route.request().url(),
        { messages: { detail: { code: 'migration.changed', params: {} } } },
        409,
      ),
    });
  });
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm undo', exact: true }).click();
  await expect.poll(() => requested).toBe(true);
  await page.evaluate((hash) => {
    location.hash = hash;
  }, `#projects/${target}/saves/migrate?task=undo-task`);
  await expect(page.getByTestId('migration-task')).toContainText('Migration undo');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await openUndo();
  const response = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/v2/tasks/save-migrations/task/undo') &&
      response.status() === 409,
  );
  release();
  await response;
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))),
      ),
  );
  await expect(page.getByRole('dialog')).not.toContainText(
    'Source or target saves or save locations changed. Preview again.',
  );
  await expect(page.getByTestId('migration-task')).toContainText('100%');
});
for (const kind of ['migration', 'undo'])
  for (const delayed of ['submission', 'status'])
    test(`${kind} finishes after early return with a delayed ${delayed} response`, async ({
      page,
    }) => {
      await prepare(page);
      let state = 'idle',
        migrated = kind === 'undo',
        taskReads = 0,
        historyReads = 0;
      let releaseSubmission!: () => void, releaseStatus!: () => void;
      const submissionGate = new Promise<void>((resolve) => {
        releaseSubmission = resolve;
      });
      const statusGate = new Promise<void>((resolve) => {
        releaseStatus = resolve;
      });
      const rows = Array.from({ length: 50 }, (_, index) => ({
        id: `slot-${index}`,
        name: `slot-${index}.save`,
        kind: 'manual',
        metadata: {},
        updated_at: new Date(1 * 1000).toISOString(),
        size_bytes: 120,
        copies: 1,
      }));
      const added = { ...rows[0], id: 'added-slot', name: 'slot-added.save' };
      const running = { ...task('running', kind), undo_id: null };
      const history = () =>
        state === 'idle'
          ? kind === 'undo'
            ? [task('completed')]
            : []
          : [
              {
                ...running,
                state,
                phase: state === 'completed' ? 'completed' : 'writing',
                completed: state === 'completed' ? 2 : 1,
              },
            ];
      await page.route(`**/api/v2/projects/${target}/saves`, (route) =>
        route.fulfill({
          json: mockEnvelope(
            route.request().url(),
            { writable: state !== 'running', saves: migrated ? [...rows, added] : rows },
            200,
          ),
        }),
      );
      await page.route(`**/api/v2/projects/${target}/saves/migrations`, async (route) => {
        if (route.request().method() === 'GET') {
          historyReads++;
          return route.fulfill({ json: mockEnvelope(route.request().url(), history(), 200) });
        }
        state = 'running';
        if (delayed === 'submission') await submissionGate;
        await route.fulfill({ json: mockEnvelope(route.request().url(), running, 200) });
      });
      if (kind === 'undo')
        await page.route('**/api/v2/tasks/save-migrations/task/undo', async (route) => {
          state = 'running';
          if (delayed === 'submission') await submissionGate;
          await route.fulfill({ json: mockEnvelope(route.request().url(), running, 200) });
        });
      await page.route(`**/api/v2/tasks/save-migrations/${running.task_id}`, async (route) => {
        taskReads++;
        await statusGate;
        await route.fulfill({ json: mockEnvelope(route.request().url(), running, 200) });
      });
      await page.goto(`/#projects/${target}/saves?search=slot&kind=manual`);
      await expect(page.getByTestId('save-row-slot-49')).toHaveCount(1);
      await page.evaluate(() => scrollTo(0, 400));
      await expect.poll(() => page.evaluate(() => scrollY)).toBe(400);
      const anchorY = await page
        .getByTestId('save-row-slot-5')
        .evaluate((node) => node.getBoundingClientRect().y);
      const retainedContext = () =>
        page
          .getByTestId('save-row-slot-5')
          .evaluate(
            (node, originalY) =>
              Math.abs(scrollY - 400) < 1 ||
              Math.abs(node.getBoundingClientRect().y - originalY) < 1,
            anchorY,
          );
      await page
        .getByRole('button', { name: 'Migrate saves', exact: true })
        .evaluate((button) => (button as HTMLButtonElement).click());
      if (kind === 'migration') {
        await selectSource(page);
        await page.getByRole('button', { name: 'Preview migration', exact: true }).click();
        await page
          .getByRole('checkbox', {
            name: 'Skip invalid files and migrate valid files only',
            exact: true,
          })
          .check();
        await page.getByRole('button', { name: 'Start migration', exact: true }).click();
      } else {
        await page
          .locator('.vn-migration-history')
          .getByRole('button', { name: 'Undo migration', exact: true })
          .click();
        await page
          .getByRole('dialog')
          .getByRole('button', { name: 'Confirm undo', exact: true })
          .click();
      }
      await expect.poll(() => state).toBe('running');
      if (delayed === 'status') {
        await expect(page.getByTestId('migration-task')).toContainText('50%');
        await expect.poll(() => taskReads).toBe(1);
        await page.getByRole('button', { name: 'Back', exact: true }).click();
      } else await page.goBack();
      await expect(page).toHaveURL(/saves\?search=slot&kind=manual$/);
      await expect(page.getByRole('searchbox')).toHaveValue('slot');
      await expect.poll(retainedContext).toBe(true);
      await expect(page.getByRole('button', { name: 'Upload save', exact: true })).toBeDisabled();
      const visibleHistoryReads = historyReads;
      await expect.poll(() => historyReads).toBeGreaterThan(visibleHistoryReads);
      const readsOnLeave = taskReads;
      state = 'completed';
      migrated = kind === 'migration';
      releaseSubmission();
      releaseStatus();
      if (kind === 'migration')
        await expect(page.getByTestId('save-row-added-slot')).toHaveCount(1, { timeout: 10000 });
      else await expect(page.getByTestId('save-row-added-slot')).toHaveCount(0, { timeout: 10000 });
      await expect(page.getByRole('button', { name: 'Upload save', exact: true })).toBeEnabled();
      await expect(page).toHaveURL(/saves\?search=slot&kind=manual$/);
      await expect.poll(retainedContext).toBe(true);
      await expect(page.getByTestId('migration-task')).toHaveCount(0);
      expect(taskReads).toBe(readsOnLeave);
    });
for (const kind of ['migration', 'undo'])
  for (const outcome of ['accepted', 'ambiguous'])
    test(`${kind} ${outcome} after leaving before enqueue restarts only visible list refresh`, async ({
      page,
    }) => {
      await prepare(page);
      let state = 'idle',
        migrated = kind === 'undo',
        taskReads = 0,
        historyReads = 0,
        terminalHistorySeen = false;
      const slot = {
        id: 'slot',
        name: '1-1.save',
        metadata: {},
        kind: 'manual',
        updated_at: new Date(1 * 1000).toISOString(),
        size_bytes: 120,
        copies: 1,
      };
      const added = { ...slot, id: 'added-slot', name: '2-1.save' };
      const running = task('running', kind);
      let release!: () => void;
      const ready = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route(`**/api/v2/projects/${target}/saves`, (route) =>
        route.fulfill({
          json: mockEnvelope(
            route.request().url(),
            { writable: state !== 'running', saves: migrated ? [slot, added] : [slot] },
            200,
          ),
        }),
      );
      const submit = async (route: import('@playwright/test').Route) => {
        await ready;
        state = 'running';
        await route.fulfill(
          outcome === 'accepted'
            ? { json: mockEnvelope(route.request().url(), running) }
            : { status: 503, json: { detail: 'POST_RESPONSE_LOST_AFTER_ACCEPTANCE' } },
        );
      };
      await page.route(`**/api/v2/projects/${target}/saves/migrations`, (route) => {
        if (route.request().method() === 'POST') return submit(route);
        historyReads++;
        if (state === 'completed') terminalHistorySeen = true;
        return route.fulfill({
          json: mockEnvelope(
            route.request().url(),
            state === 'idle'
              ? kind === 'undo'
                ? [task('completed')]
                : []
              : [{ ...running, state }],
            200,
          ),
        });
      });
      if (kind === 'undo') await page.route('**/api/v2/tasks/save-migrations/task/undo', submit);
      await page.route(`**/api/v2/tasks/save-migrations/${running.task_id}`, (route) => {
        taskReads++;
        return route.fulfill({ json: mockEnvelope(route.request().url(), running, 200) });
      });
      await page.reload();
      if (kind === 'migration') {
        await openPreview(page);
        await page
          .getByRole('checkbox', {
            name: 'Skip invalid files and migrate valid files only',
            exact: true,
          })
          .check();
        await page.getByRole('button', { name: 'Start migration', exact: true }).click();
      } else {
        await page.getByRole('button', { name: 'Migrate saves', exact: true }).click();
        await page
          .locator('.vn-migration-history')
          .getByRole('button', { name: 'Undo migration', exact: true })
          .click();
        await page
          .getByRole('dialog')
          .getByRole('button', { name: 'Confirm undo', exact: true })
          .click();
      }
      const idleHistory = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/v2/projects/${target}/saves/migrations`) &&
          response.request().method() === 'GET',
      );
      await page.goBack();
      await expect(page).toHaveURL(new RegExp(`/saves$`));
      await idleHistory;
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))),
          ),
      );
      await expect(page.getByRole('button', { name: 'Upload save', exact: true })).toBeEnabled();
      const oldHistoryReads = historyReads;
      release();
      await expect.poll(() => state).toBe('running');
      await expect(page.getByRole('button', { name: 'Upload save', exact: true })).toBeDisabled();
      await expect.poll(() => historyReads).toBeGreaterThan(oldHistoryReads);
      state = 'completed';
      migrated = kind === 'migration';
      if (kind === 'migration')
        await expect(page.getByTestId('save-row-added-slot')).toHaveCount(1, { timeout: 10000 });
      else await expect(page.getByTestId('save-row-added-slot')).toHaveCount(0, { timeout: 10000 });
      await expect(page.getByRole('button', { name: 'Upload save', exact: true })).toBeEnabled();
      await expect(page).toHaveURL(new RegExp(`/saves$`));
      await expect(page.getByTestId('migration-task')).toHaveCount(0);
      expect(taskReads).toBe(0);
      await expect.poll(() => terminalHistorySeen).toBe(true);
      await page.evaluate(
        () =>
          new Promise((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))),
          ),
      );
      const terminalHistoryReads = historyReads;
      await page.waitForTimeout(1500);
      expect(historyReads).toBe(terminalHistoryReads);
    });
test('a submission accepted after leaving the saves tab refreshes the reopened list', async ({
  page,
}) => {
  await prepare(page);
  let state: 'idle' | 'running' | 'completed' = 'idle';
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const slot = {
    id: 'migrated-slot',
    name: 'migrated.save',
    kind: 'manual',
    metadata: {},
    updated_at: new Date(1 * 1000).toISOString(),
    size_bytes: 120,
    copies: 1,
  };
  await page.route(`**/api/v2/projects/${target}/saves`, (route) =>
    route.fulfill({
      json: mockEnvelope(route.request().url(), {
        writable: state !== 'running',
        saves: state === 'completed' ? [slot] : [],
      }),
    }),
  );
  await page.route(`**/api/v2/projects/${target}/saves/migrations`, async (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({
        json: mockEnvelope(route.request().url(), state === 'idle' ? [] : [task(state)]),
      });
    }
    await pending;
    state = 'running';
    return route.fulfill({ json: mockEnvelope(route.request().url(), task(state)) });
  });
  await openPreview(page);
  await page
    .getByRole('checkbox', { name: 'Skip invalid files and migrate valid files only' })
    .check();
  const submission = page.waitForRequest(
    (request) => request.method() === 'POST' && request.url().endsWith('/saves/migrations'),
  );
  await page.getByRole('button', { name: 'Start migration', exact: true }).click();
  await submission;
  await page.goto(`/#projects/${target}`);
  await expect(page.locator('.vn-migration-page')).toHaveCount(0);
  const idleHistory = page.waitForResponse(
    (response) =>
      response.request().method() === 'GET' && response.url().endsWith('/saves/migrations'),
  );
  await page.goto(`/#projects/${target}/saves`);
  await idleHistory;
  const upload = page.getByRole('button', { name: 'Upload save', exact: true });
  await expect(upload).toBeEnabled();
  release();
  await expect(upload).toBeDisabled();
  state = 'completed';
  await expect(page.getByTestId('save-row-migrated-slot')).toBeVisible();
  await expect(upload).toBeEnabled();
  await expect(page).toHaveURL(/\/saves$/);
});
for (const width of [320, 390])
  test(`100% progress stays on one line at ${width}px and 200% zoom`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await prepare(page);
    await page.route('**/api/v2/tasks/save-migrations/task', (route) =>
      route.fulfill({ json: mockEnvelope(route.request().url(), task('completed'), 200) }),
    );
    await page.goto(`/#projects/${target}/saves/migrate?task=task`);
    const percent = page.getByTestId('migration-task').locator('.vn-progress-percent');
    await expect(percent).toHaveText('100%');
    await page.evaluate(() => document.fonts.ready);
    for (const zoom of ['1', '2']) {
      await page.locator('body').evaluate((body, value) => (body.style.zoom = value), zoom);
      const geometry = await percent.evaluate((node) => {
        const range = document.createRange();
        range.selectNodeContents(node);
        const rect = node.getBoundingClientRect();
        const text = range.getBoundingClientRect();
        return {
          lines: new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size,
          inside: text.left >= rect.left - 1 && text.right <= rect.right + 1,
        };
      });
      expect(geometry.lines).toBe(1);
      expect(geometry.inside).toBe(true);
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        .toBe(true);
    }
  });
