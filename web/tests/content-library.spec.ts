import { mockEnvelope, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect } from '@playwright/test';
import { signIn } from './fixtures.js';
const game = renpyProjectFixture({
  project_id: 'c'.repeat(32),
  name: 'Content fixture',
  path: '/projects/fixture',
  engine_version: '8.3.2',
  release_version: '1.0',
  size_bytes: 4096,
  recommendation: { build_version: '8.3.2', kind: 'exact', runtime_id: 'renpy:8.3.2' },
});
async function prepare(page: import('@playwright/test').Page) {
  await signIn(page);
  let flag = false;
  const calls = { deletions: 0, restores: 0 };
  await page.route('**/api/v2/**', (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname.split('/api/v2/')[1];
    if (path.endsWith('/backups/backup/restore')) {
      calls.restores++;
      flag = false;
      return route.fulfill({
        json: mockEnvelope(route.request().url(), { revision: 'b'.repeat(64) }, 200),
      });
    }
    if (path.endsWith('/saves/slot/backups'))
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          [{ id: 'backup', created_at: new Date(1 * 1000).toISOString() }],
          200,
        ),
      });
    if (route.request().method() === 'DELETE') {
      calls.deletions++;
      return route.fulfill({ json: mockEnvelope(route.request().url(), {}, 200) });
    }
    if (path.endsWith('/variables') && route.request().method() === 'PATCH') {
      flag = true;
      return route.fulfill({
        json: mockEnvelope(route.request().url(), { revision: 'b'.repeat(64) }, 200),
      });
    }
    const fields = [
      { id: 'flag', name: 'store.flag', type: 'boolean', value: flag },
      {
        id: 'money',
        name: 'store.money',
        type: 'number',
        numeric_type: 'integer',
        value: '36893488147419103232',
      },
    ];
    const responses: Record<string, unknown> = {
      projects: [game],
      session: { state: 'stopped' },
      'imports/active': null,
      'uploads/directories/active': null,
      engines: engineFixtures,
    };
    if (path === `projects/${game.project_id}/assets`)
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            ready: true,
            counts: { image: 2, audio: 1, video: 0, script: 1, other: 0 },
            size_bytes: 4096,
            updated_at: new Date(1 * 1000).toISOString(),
            job: null,
          },
          200,
        ),
      });
    if (path === `projects/${game.project_id}/assets/entries`)
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            entries: [
              { id: 'folder:images', path: 'images', name: 'images', directory: true, count: 2 },
            ],
            total: 1,
          },
          200,
        ),
      });
    if (path === `projects/${game.project_id}/saves`)
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            writable: true,
            saves: [
              {
                id: 'slot',
                name: '1-1-LT1.save',
                kind: 'manual',
                updated_at: new Date(1 * 1000).toISOString(),
                size_bytes: 2048,
                copies: 1,
                thumbnail: false,
                metadata: {},
              },
            ],
          },
          200,
        ),
      });
    if (path === `projects/${game.project_id}/saves/slot`)
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            id: 'slot',
            name: '1-1-LT1.save',
            kind: 'manual',
            updated_at: new Date(1 * 1000).toISOString(),
            size_bytes: 2048,
            copies: 1,
            thumbnail: false,
            metadata: {},
            archived: false,
            writable: true,
          },
          200,
        ),
      });
    if (path === `projects/${game.project_id}/saves/slot/variables`)
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            fields: fields.filter(
              (f) =>
                !url.searchParams.get('search') || f.name.includes(url.searchParams.get('search')!),
            ),
            total: 2,
            revision: 'a'.repeat(64),
            editable: true,
            writable: true,
            reason: null,
            counts: { boolean: 1, text: 0, number: 1 },
          },
          200,
        ),
      });
    return route.fulfill({
      json: mockEnvelope(route.request().url(), responses[path] ?? null, 200),
    });
  });
  await page.goto(`/#projects/${game.project_id}`);
  return calls;
}
test('mobile media navigation and save drafts survive language changes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepare(page);
  await page.getByText('素材', { exact: true }).click();
  await expect(page.getByTestId('asset-category-image')).toBeVisible();
  await page.getByText('存档', { exact: true }).click();
  await page.getByTestId('save-row-slot').click();
  await expect(page.locator('.hero')).toBeHidden();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await expect(page.getByText('1 项待保存')).toBeVisible();
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
  await expect(page.getByText('1 pending changes')).toBeVisible();
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByText('0 pending changes')).toBeVisible();
  await page.screenshot({ path: '../var/media-saves-work/save-mobile.png', fullPage: true });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
});
test('draft guard prevents accidental navigation away from a save', async ({ page }) => {
  await prepare(page);
  await page.getByText('存档', { exact: true }).click();
  await page.getByTestId('save-row-slot').click();
  await expect(page.locator('.hero')).toBeHidden();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await page.locator('#back').click();
  await expect(page.getByRole('dialog', { name: '有未保存的修改，是否放弃？' })).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
});
test('returning to the library from the brand respects the save draft', async ({ page }) => {
  await prepare(page);
  await page.getByText('存档', { exact: true }).click();
  await page.getByTestId('save-row-slot').click();
  const detail = page.url();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  const brand = page.getByRole('banner').getByRole('button', { name: 'VN Stream', exact: true });
  const dialog = page.getByRole('dialog', { name: '有未保存的修改，是否放弃？' });
  await brand.click();
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page).toHaveURL(detail);
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
  await brand.click();
  await dialog.getByRole('button', { name: '确定', exact: true }).click();
  await expect(page.locator('h1')).toHaveText('我的游戏');
  expect(new URL(page.url()).hash).toBe('');
});
test('cancelling unsaved confirmation never deletes the save', async ({ page }) => {
  const calls = await prepare(page);
  await page.getByText('存档', { exact: true }).click();
  await page.getByTestId('save-row-slot').click();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await page.getByRole('button', { name: '删除存档', exact: true }).click();
  await page
    .getByRole('dialog', { name: '删除存档', exact: true })
    .getByRole('button', { name: '确定', exact: true })
    .click();
  await expect(page.getByRole('dialog', { name: '有未保存的修改，是否放弃？' })).toBeVisible();
  expect(calls.deletions).toBe(0);
  await page
    .getByRole('dialog', { name: '有未保存的修改，是否放弃？' })
    .getByRole('button', { name: '取消', exact: true })
    .click();
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
});
test('delete confirmation retains the revision reviewed before it opened', async ({ page }) => {
  await prepare(page);
  const slotPath = `**/api/v2/projects/${game.project_id}/saves/slot`;
  let revision = 'a'.repeat(64);
  let submittedRevision: string | undefined;
  await page.route(`${slotPath}/variables`, (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { revision }) }),
  );
  await page.route(slotPath, (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    submittedRevision = route.request().postDataJSON().revision;
    return route.fulfill({
      status: 409,
      json: mockEnvelope(
        route.request().url(),
        { error: { code: 'saves.changed', params: {} } },
        409,
      ),
    });
  });
  await page.goto(`/#projects/${game.project_id}/saves/item/slot`);
  await page.getByRole('button', { name: '删除存档', exact: true }).click();
  const confirmation = page.getByRole('dialog', { name: '删除存档', exact: true });
  await expect(confirmation).toBeVisible();
  revision = 'b'.repeat(64);
  await confirmation.getByRole('button', { name: '确定', exact: true }).click();
  await expect.poll(() => submittedRevision).toBe('a'.repeat(64));
  await expect(page).toHaveURL(/\/saves\/item\/slot$/);
});
test('restore also asks before discarding a variable draft', async ({ page }) => {
  const calls = await prepare(page);
  await page.getByText('存档', { exact: true }).click();
  await page.getByTestId('save-row-slot').click();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await page.getByRole('button', { name: '备份历史', exact: true }).click();
  await page.getByRole('button', { name: '恢复此备份', exact: true }).click();
  await page
    .getByRole('dialog', { name: '恢复此备份', exact: true })
    .getByRole('button', { name: '确定', exact: true })
    .click();
  await expect(page.getByRole('dialog', { name: '有未保存的修改，是否放弃？' })).toBeVisible();
  expect(calls.restores).toBe(0);
  await page
    .getByRole('dialog', { name: '有未保存的修改，是否放弃？' })
    .getByRole('button', { name: '取消', exact: true })
    .click();
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
});
test('cancelling browser Back preserves the original list history entry', async ({ page }) => {
  await prepare(page);
  await page.getByText('存档', { exact: true }).click();
  const listing = page.url();
  await page.getByTestId('save-row-slot').click();
  const detail = page.url();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await page.goBack();
  await page
    .getByRole('dialog', { name: '有未保存的修改，是否放弃？' })
    .getByRole('button', { name: '取消', exact: true })
    .click();
  await expect(page).toHaveURL(detail);
  await page.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect(page.getByText('0 项待保存')).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(listing);
  await expect(page.getByTestId('save-row-slot')).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(detail);
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeVisible();
});
test('accepting browser Back discards drafts without losing forward navigation', async ({
  page,
}) => {
  await prepare(page);
  await page.getByText('存档', { exact: true }).click();
  const listing = page.url();
  await page.getByTestId('save-row-slot').click();
  const detail = page.url();
  await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
  await page.goBack();
  await page
    .getByRole('dialog', { name: '有未保存的修改，是否放弃？' })
    .getByRole('button', { name: '确定', exact: true })
    .click();
  await expect(page).toHaveURL(listing);
  await expect(page.getByTestId('save-row-slot')).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(detail);
  await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).not.toBeChecked();
});
