import { mockEnvelope, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect, type Page } from './fixtures.js';
const id = 'e'.repeat(32);
async function prepare(
  page: Page,
  running = false,
  saveCapabilities = { save_editing: true, save_migration: true },
) {
  const fixture = renpyProjectFixture({
    project_id: id,
    path: '/projects/a/very-long-project-folder/game-project',
    engine_version: '8.3.2',
    release_version: '1.0',
    size_bytes: 1234,
    recommendation: { build_version: '8.3.2', kind: 'exact', runtime_id: 'renpy:8.3.2' },
  });
  const game = { ...fixture, capabilities: { ...fixture.capabilities, ...saveCapabilities } };
  await page.route('**/api/v2/**', async (route) => {
    const path = new URL(route.request().url()).pathname.split('/api/v2/')[1];
    let data: unknown = null;
    if (path === 'projects') data = [game];
    if (path === 'engines') data = engineFixtures;
    if (path === 'session') data = { state: 'stopped' };
    if (path === `projects/${id}/saves`)
      data = {
        writable: true,
        saves: [
          {
            id: 'slot',
            name: '1-1.save',
            kind: 'manual',
            metadata: {},
            size_bytes: 1234,
            updated_at: new Date(1 * 1000).toISOString(),
            copies: 1,
            thumbnail: false,
          },
        ],
      };
    if (path === `projects/${id}/saves/signing`)
      data = { state: 'completed', signed: 1, skipped: 0, failed: 0, total: 1 };
    if (path === `projects/${id}/saves/slot`)
      data = {
        id: 'slot',
        name: '1-1.save',
        kind: 'manual',
        metadata: {},
        size_bytes: 1234,
        updated_at: new Date(1 * 1000).toISOString(),
        copies: 1,
        writable: true,
      };
    if (path === `projects/${id}/saves/slot/variables`)
      data = {
        revision: 'a'.repeat(64),
        editable: true,
        writable: true,
        fields: [{ id: 'flag', name: 'store.flag', type: 'boolean', value: false }],
        total: 1,
        counts: { boolean: 465, text: 9, number: 18 },
      };
    if (path === `projects/${id}/saves/slot/backups`)
      data =
        route.request().method() === 'POST'
          ? { id: 'backup', kind: 'manual', created_at: new Date(1 * 1000).toISOString() }
          : [];
    if (path === `projects/${id}/assets`)
      data = {
        ready: true,
        counts: {},
        size_bytes: 0,
        job: running
          ? {
              task_id: 'job',
              state: 'running',
              phase: 'indexing',
              total_bytes: 100,
              completed_bytes: 75,
            }
          : null,
      };
    if (path === 'tasks/assets/job')
      data = {
        task_id: 'job',
        state: 'running',
        phase: 'indexing',
        total_bytes: 100,
        completed_bytes: 75,
      };
    await route.fulfill({ json: mockEnvelope(route.request().url(), data, 200) });
  });
  await page.goto(`/#projects/${id}`);
}
for (const width of [320, 390, 1024]) {
  test(`inline filters and path at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await prepare(page);
    await expect(page.getByText("Ren'Py 8.3.2", { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '复制服务器路径' })).toHaveCount(0);
    await expect(
      page.getByText('/projects/a/very-long-project-folder/game-project', { exact: true }),
    ).toBeVisible();
    await page.getByText('存档', { exact: true }).click();
    await expect(page.locator('.vn-filter-strip')).toBeVisible();
    await page.getByTestId('save-row-slot').click();
    await expect(page.getByRole('button', { name: '备份当前存档' })).toBeVisible();
    await expect(
      page.getByText('仅编辑已有简单游戏变量，不修改复杂对象和引擎内部状态。'),
    ).toHaveCount(0);
    const positions = await page
      .locator('.vn-save-editor .vn-filter-label:visible')
      .evaluateAll((nodes) =>
        nodes.map((node) => {
          const [label, count] = [...node.children].map((n) => n.getBoundingClientRect());
          return {
            dy: Math.abs(label.y + label.height / 2 - count.y - count.height / 2),
            gap: count.x - label.right,
          };
        }),
      );
    expect(positions.length).toBe(4);
    for (const p of positions) {
      expect(p.dy).toBeLessThanOrEqual(1);
      expect(p.gap).toBeLessThanOrEqual(5);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    if (width === 390)
      await page.screenshot({ path: '../var/save-polish-work/mobile-editor.png', fullPage: true });
  });
}
for (const language of ['zh', 'en'])
  for (const appearance of ['light', 'dark']) {
    test(`mobile filters retain drafts in ${language} ${appearance} and 200% zoom`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 320, height: 844 });
      await page.addInitScript(
        ({ language, appearance }) => {
          localStorage.setItem(
            'vn-stream:preferences',
            JSON.stringify({
              ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
              language: language,
            }),
          );
          localStorage.setItem(
            'vn-stream:preferences',
            JSON.stringify({
              ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
              appearance: appearance,
            }),
          );
        },
        { language, appearance },
      );
      await prepare(page);
      await page.locator('.vn-details-tabs .ant-segmented-item').nth(2).click();
      const filters = page.locator('.vn-filter-strip:visible');
      await filters.locator('.ant-segmented-item').last().click();
      await expect(filters.getByRole('radio').last()).toBeChecked();
      expect(await filters.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
      await filters.locator('.ant-segmented-item').first().click();
      await page.getByTestId('save-row-slot').click();
      await page.locator('.vn-variable-row').getByRole('switch').click();
      await page
        .getByRole('button', {
          name: language === 'en' ? 'Back up current save' : '备份当前存档',
          exact: true,
        })
        .click();
      await expect(page.locator('.vn-variable-row').getByRole('switch')).toBeChecked();
      await page.locator('body').evaluate((node) => (node.style.zoom = '2'));
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
        .toBe(true);
      const centers = await page
        .locator('.vn-save-editor .vn-filter-label:visible')
        .evaluateAll((nodes) =>
          nodes.map((n) =>
            [...n.children].map((c) => {
              const r = c.getBoundingClientRect();
              return r.y + r.height / 2;
            }),
          ),
        );
      for (const pair of centers) expect(Math.abs(pair[0] - pair[1])).toBeLessThanOrEqual(1);
    });
  }
test('progress and cancellation share one centered row', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await prepare(page, true);
  await page.getByText('素材', { exact: true }).click();
  const row = page.locator('.vn-progress-row:visible');
  await expect(row.getByRole('button', { name: '取消', exact: true })).toBeVisible();
  const centers = await row.locator('.vn-progress-percent,.ant-btn').evaluateAll((nodes) =>
    nodes.map((n) => {
      const r = n.getBoundingClientRect();
      return r.y + r.height / 2;
    }),
  );
  expect(Math.max(...centers) - Math.min(...centers)).toBeLessThanOrEqual(1);
  await expect(row).toContainText('75%');
});
test('open editor refreshes permissions and clean revision after signing', async ({ page }) => {
  await prepare(page);
  let completed = false;
  let submitted: unknown;
  await page.route(`**/api/v2/projects/${id}/saves/signing`, (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          state: completed ? 'completed' : 'waiting',
          total: 1,
          signed: completed ? 1 : 0,
          skipped: 0,
          failed: 0,
        },
        200,
      ),
    }),
  );
  await page.route(`**/api/v2/projects/${id}/saves/slot/variables*`, async (route) => {
    if (route.request().method() === 'PATCH') {
      submitted = route.request().postDataJSON();
      return route.fulfill({
        json: mockEnvelope(route.request().url(), { revision: 'c'.repeat(64) }, 200),
      });
    }
    return route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          revision: (completed ? 'b' : 'a').repeat(64),
          editable: completed,
          writable: completed,
          fields: [{ id: 'flag', name: 'store.flag', type: 'boolean', value: false }],
          total: 1,
          counts: { boolean: 1, text: 0, number: 0 },
        },
        200,
      ),
    });
  });
  await page.getByText('存档', { exact: true }).click();
  await page.getByTestId('save-row-slot').click();
  const toggle = page.getByRole('switch', { name: 'store.flag', exact: true });
  await expect(toggle).toBeDisabled();
  completed = true;
  await expect(toggle).toBeEnabled({ timeout: 10000 });
  await toggle.click();
  await page.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect.poll(() => submitted).toMatchObject({ revision: 'b'.repeat(64) });
});
test('a second edit waits for the committed revision to be read', async ({ page }) => {
  await prepare(page);
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  const changes: { revision: string }[] = [];
  let refreshing = false;
  const fields = [{ id: 'flag', name: 'store.flag', type: 'boolean' }];
  await page.route(`**/api/v2/projects/${id}/saves/slot/variables*`, async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'PATCH') {
      changes.push(route.request().postDataJSON());
      return route.fulfill({ json: mockEnvelope(url, { revision: 'b'.repeat(64) }) });
    }
    if (changes.length) {
      refreshing = true;
      await ready;
    }
    await route.fulfill({
      json: mockEnvelope(url, {
        revision: (changes.length ? 'b' : 'a').repeat(64),
        editable: true,
        writable: true,
        fields: fields.map((field) => ({ ...field, value: changes.length === 1 })),
        total: 1,
        counts: { boolean: 1 },
      }),
    });
  });
  await page.goto(`/#projects/${id}/saves/item/slot`);
  const toggle = page.getByRole('switch', { name: 'store.flag', exact: true });
  const save = page.getByRole('button', { name: '保存修改', exact: true });
  await toggle.click();
  await save.click();
  await expect.poll(() => refreshing).toBe(true);
  await expect(toggle).toBeDisabled();
  release();
  await expect(toggle).toBeChecked();
  await toggle.click();
  await save.click();
  await expect
    .poll(() => changes.map(({ revision }) => revision))
    .toEqual(['a'.repeat(64), 'b'.repeat(64)]);
});
test('management works with editing and migration explicitly unavailable', async ({ page }) => {
  await prepare(page, false, { save_editing: false, save_migration: false });
  const unsupportedReads: string[] = [];
  await page.route(`**/api/v2/projects/${id}/saves/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (/\/(signing|migrations|migration-sources|variables)$/.test(path))
      unsupportedReads.push(path);
    return route.fallback();
  });
  await page.goto(`/#projects/${id}/saves`);
  await expect(page.getByRole('button', { name: '迁移存档', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '上传存档', exact: true })).toBeEnabled();
  await page.getByTestId('save-row-slot').click();
  await expect(page.getByRole('link', { name: '下载', exact: true })).toBeVisible();
  await expect(page.locator('.vn-save-editor')).toHaveCount(0);
  await page.getByRole('button', { name: '备份历史', exact: true }).click();
  await expect(page.locator('.vn-backup-list .ant-empty')).toBeVisible();
  expect(unsupportedReads).toEqual([]);
});
for (const width of [320, 390, 844, 1280])
  for (const language of ['zh', 'en']) {
    test(`save actions and compact rows fit ${width}px in ${language}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await page.addInitScript(
        (language) =>
          localStorage.setItem(
            'vn-stream:preferences',
            JSON.stringify({
              ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
              language: language,
            }),
          ),
        language,
      );
      await prepare(page);
      await page.locator('.vn-details-tabs .ant-segmented-item').nth(2).click();
      await page.getByTestId('save-row-slot').click();
      const row = page.locator('.vn-variable-row');
      const box = await row.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.height).toBeLessThanOrEqual(48);
      const toggle = row.getByRole('switch');
      await toggle.click();
      const actions = page.locator('.vn-save-editor-actions');
      const discard = actions.getByRole('button', {
        name: language === 'en' ? 'Discard changes' : '放弃修改',
        exact: true,
      });
      const save = actions.getByRole('button', {
        name: language === 'en' ? 'Save changes' : '保存修改',
        exact: true,
      });
      await expect(discard).toHaveText(language === 'en' ? 'Discard' : '放弃');
      await expect(save).toHaveText(language === 'en' ? 'Save' : '保存');
      for (const zoom of ['1', '2']) {
        await page.locator('body').evaluate((body, zoom) => (body.style.zoom = zoom), zoom);
        await save.scrollIntoViewIfNeeded();
        const geometry = await actions.getByRole('button').evaluateAll((buttons) =>
          buttons.map((button) => {
            const rect = button.getBoundingClientRect();
            const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
            const tops = [];
            while (walker.nextNode()) {
              if (!walker.currentNode.textContent?.trim()) continue;
              const range = document.createRange();
              range.selectNodeContents(walker.currentNode);
              tops.push(...[...range.getClientRects()].map((r) => r.top));
            }
            return {
              height: rect.height,
              width: rect.width,
              lines: new Set(tops.map((top) => Math.round(top))).size,
              inside: rect.left >= 0 && rect.right <= innerWidth + 1,
            };
          }),
        );
        for (const button of geometry) {
          expect(button.height).toBeGreaterThanOrEqual(44 * Number(zoom));
          expect(button.width).toBeGreaterThanOrEqual(44 * Number(zoom));
          expect(button.lines).toBe(1);
          expect(button.inside).toBe(true);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await expect(toggle).toBeChecked();
      }
    });
  }
for (const language of ['zh', 'en'])
  for (const appearance of ['light', 'dark'])
    test(`backup history separates metadata in ${language} ${appearance}`, async ({ page }) => {
      await page.setViewportSize({ width: 320, height: 844 });
      await page.addInitScript(
        ({ language, appearance }) => {
          localStorage.setItem(
            'vn-stream:preferences',
            JSON.stringify({
              ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
              language: language,
            }),
          );
          localStorage.setItem(
            'vn-stream:preferences',
            JSON.stringify({
              ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
              appearance: appearance,
            }),
          );
        },
        { language, appearance },
      );
      await prepare(page);
      await page.route(`**/api/v2/projects/${id}/saves/slot/backups`, (route) =>
        route.fulfill({
          json: mockEnvelope(
            route.request().url(),
            [
              {
                id: 'sign',
                kind: 'sign',
                created_at: new Date(3 * 1000).toISOString(),
                size_bytes: 124416,
                name: '1-1.save',
              },
              {
                id: 'manual',
                kind: 'manual',
                created_at: new Date(2 * 1000).toISOString(),
                size_bytes: 1234,
                name: '1-1.save',
              },
              {
                id: 'legacy',
                created_at: new Date(1 * 1000).toISOString(),
                size_bytes: 100,
                name: '1-1.save',
              },
            ],
            200,
          ),
        }),
      );
      await page.locator('.vn-details-tabs .ant-segmented-item').nth(2).click();
      await page.getByTestId('save-row-slot').click();
      const title = language === 'en' ? 'Backup history' : '备份历史';
      await page.getByRole('button', { name: title, exact: true }).click();
      const history = page.getByRole('list', { name: title, exact: true });
      await expect(history.getByRole('listitem')).toHaveCount(3);
      const first = history.getByRole('listitem').first();
      await expect(first.locator('.ant-tag')).toHaveText(
        language === 'en' ? ['Automatic backup', 'before signing'] : ['自动备份', '签名前'],
      );
      await expect(history.getByRole('listitem').nth(1).locator('.ant-tag')).toHaveText([
        language === 'en' ? 'Manual backup' : '手动备份',
      ]);
      for (const width of [320, 390, 844, 1280]) {
        await page.setViewportSize({ width, height: 844 });
        for (const zoom of ['1', '2']) {
          await page.locator('body').evaluate((body, zoom) => (body.style.zoom = zoom), zoom);
          for (const item of await history.getByRole('listitem').all()) {
            const row = await item.boundingBox();
            for (const button of await item.locator('.ant-btn').all()) {
              const box = await button.boundingBox();
              expect(box!.height).toBeGreaterThanOrEqual(44 * Number(zoom));
              expect(box!.x).toBeGreaterThanOrEqual(row!.x);
              expect(box!.x + box!.width).toBeLessThanOrEqual(row!.x + row!.width + 1);
            }
          }
          expect(
            await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          ).toBe(true);
        }
      }
      await page.locator('body').evaluate((body) => (body.style.zoom = '1'));
      await page.setViewportSize({ width: 390, height: 844 });
      await history.screenshot({
        path: `../var/save-ui-work/history-${language}-${appearance}.png`,
      });
    });
for (const state of ['loading', 'empty', 'error', 'readonly'])
  test(`backup history handles ${state} without exposing technical errors`, async ({ page }) => {
    await prepare(page);
    let release!: () => void;
    const ready = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/v2/projects/${id}/saves/slot/backups`, async (route) => {
      if (state === 'loading') await ready;
      await route.fulfill(
        state === 'error'
          ? { status: 500, json: { detail: 'INTERNAL_EXCEPTION_MUST_NOT_SHOW' } }
          : {
              json: mockEnvelope(
                route.request().url(),
                state === 'readonly'
                  ? [{ id: 'old', created_at: new Date(1 * 1000).toISOString(), size_bytes: 1234 }]
                  : [],
              ),
            },
      );
    });
    if (state === 'readonly')
      await page.route(`**/api/v2/projects/${id}/saves`, (route) =>
        route.fulfill({
          json: mockEnvelope(route.request().url(), { writable: false, saves: [] }, 200),
        }),
      );
    await page.locator('.vn-details-tabs .ant-segmented-item').nth(2).click();
    await page.goto(`/#projects/${id}/saves/item/slot`);
    await page.getByRole('button', { name: '备份历史', exact: true }).click();
    const history = page.locator('.vn-backup-list');
    if (state === 'loading') {
      await expect(history.getByRole('list', { name: '备份历史', exact: true })).toHaveAttribute(
        'aria-busy',
        'true',
      );
      release();
      await expect(history.getByRole('list', { name: '备份历史', exact: true })).toHaveAttribute(
        'aria-busy',
        'false',
      );
    }
    if (state === 'empty' || state === 'loading')
      await expect(history.locator('.ant-empty')).toBeVisible();
    if (state === 'error') {
      await expect(history.getByRole('alert')).toBeVisible();
      await expect(history).not.toContainText('INTERNAL_EXCEPTION_MUST_NOT_SHOW');
    }
    if (state === 'readonly') {
      await expect(history.getByRole('button', { name: '恢复此备份' })).toBeDisabled();
      await expect(history.getByRole('link', { name: '下载' })).toBeVisible();
    }
  });
test('adjacent compact switches and text/number editors keep separate touch targets', async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await prepare(page);
  await page.route(`**/api/v2/projects/${id}/saves/slot/variables*`, (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          revision: 'a'.repeat(64),
          editable: true,
          writable: true,
          reason: null,
          total: 4,
          counts: { boolean: 2, number: 1, text: 1 },
          fields: [
            { id: 'first', name: 'store.first', type: 'boolean', value: false },
            { id: 'second', name: 'store.second', type: 'boolean', value: false },
            { id: 'money', name: 'store.money', type: 'number', numeric_type: 'int', value: '120' },
            {
              id: 'name',
              name: 'store.a_long_variable_name_that_must_wrap_without_overlapping_the_value',
              type: 'text',
              value: 'Original',
            },
          ],
        },
        200,
      ),
    }),
  );
  await page.locator('.vn-details-tabs .ant-segmented-item').nth(2).click();
  await page.getByTestId('save-row-slot').click();
  const rows = page.locator('.vn-variable-row');
  await expect(rows).toHaveCount(4);
  for (const index of [0, 1]) {
    await rows.nth(index).scrollIntoViewIfNeeded();
    const row = await rows.nth(index).boundingBox();
    const toggle = await rows.nth(index).getByRole('switch').boundingBox();
    expect(row!.height).toBe(44);
    await page.mouse.click(
      toggle!.x + toggle!.width / 2,
      index === 0 ? row!.y + row!.height - 1 : row!.y + 1,
    );
    await expect(rows.nth(index).getByRole('switch')).toBeChecked();
  }
  await expect(rows.nth(0).getByRole('switch')).toBeChecked();
  for (const index of [2, 3]) {
    const button = rows.nth(index).getByRole('button');
    await button.scrollIntoViewIfNeeded();
    const control = await button.boundingBox();
    const row = await rows.nth(index).boundingBox();
    expect(control!.height).toBeGreaterThanOrEqual(44);
    expect(control!.y).toBeGreaterThanOrEqual(row!.y);
    expect(control!.y + control!.height).toBeLessThanOrEqual(row!.y + row!.height);
    await button.click();
    await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue(
      index === 2 ? '120' : 'Original',
    );
    await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click();
  }
  await page.screenshot({ path: '../var/save-ui-work/compact-editor-320.png', fullPage: true });
});
test('bootstrap compact spacing resolves before the application script loads', async ({ page }) => {
  await page.route('**/main.js', (route) => route.abort());
  await page.goto('/');
  const padding = await page.evaluate(() => {
    const probe = document.createElement('div');
    probe.style.padding = 'var(--vn-space-xxs)';
    document.body.append(probe);
    const value = getComputedStyle(probe).padding;
    probe.remove();
    return value;
  });
  expect(padding).toBe('4px');
});
