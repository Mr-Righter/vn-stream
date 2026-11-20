import { readFile } from 'node:fs/promises';
import { test, expect } from './fixtures.js';

test('real migration UI copies source progress and confirms whole-batch undo', async ({
  page,
  request,
}) => {
  test.setTimeout(120000);
  const fixtures = JSON.parse(
    await readFile('../var/save-migration-work/ui-fixtures.json', 'utf8'),
  );
  const games: { id: string; name: string }[] = [];
  try {
    for (const fixture of fixtures) {
      const response = await request.post('/api/v2/projects', {
        data: { path: '/projects/' + fixture.folder },
      });
      expect(response.status()).toBe(201);
      games.push(
        await response
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body)),
      );
    }
    const [source, target] = games;
    const prefix = `/api/v2/projects/${target.project_id}/saves`;
    const listing = await (
      await request.get(prefix)
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    const uid = listing.saves.find((item: { name: string }) => item.name === 'slot.save').id;
    const money = async () =>
      (
        await (
          await request.get(`${prefix}/${uid}/variables`)
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).fields.find((field: { name: string }) => field.name === 'store.money').value;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/library/#projects/${target.project_id}/saves`);
    await page.getByRole('button', { name: '迁移存档', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#projects/${target.project_id}/saves/migrate$`));
    const selection = page.getByRole('combobox', { name: '来源项目', exact: true });
    await selection.click();
    await expect(selection).not.toBeEditable();
    await selection.press('Escape');
    await page.getByRole('searchbox', { name: '搜索来源项目', exact: true }).fill(source.name);
    await selection.click();
    await page.getByRole('option').filter({ hasText: source.name }).click();
    await expect(
      page.getByRole('checkbox', { name: '包含 persistent 数据', exact: true }),
    ).toBeChecked();
    await page.getByRole('button', { name: '预览迁移', exact: true }).click();
    await expect(page.getByRole('button', { name: '开始迁移', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '开始迁移', exact: true }).click();
    await expect.poll(money).toBe('42');
    await expect(page).toHaveURL(/\?task=/);
    await page.reload();
    await expect(page.getByTestId('migration-task')).toBeVisible({ timeout: 30000 });
    for (const width of [320, 390, 844, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    const undo = page
      .locator('.vn-migration-history')
      .getByRole('button', { name: 'Undo migration', exact: true });
    await expect(undo).toBeEnabled();
    await undo.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
    expect(await money()).toBe('42');
    await undo.click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Confirm undo', exact: true })
      .click();
    await expect.poll(money).toBe('5');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByTestId('migration-task')).toContainText('100%');
    await page.screenshot({
      path: '../var/save-migration-work/container-migration-ui.png',
      fullPage: true,
    });
    const sourceListing = await (
      await request.get(`/api/v2/projects/${source.project_id}/saves`)
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    const sourceUid = sourceListing.saves.find(
      (item: { name: string }) => item.name === 'slot.save',
    ).id;
    const sourceFields = await (
      await request.get(`/api/v2/projects/${source.project_id}/saves/${sourceUid}/variables`)
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(
      sourceFields.fields.find((field: { name: string }) => field.name === 'store.money').value,
    ).toBe('42');
  } finally {
    await page.goto('/library/');
    for (const game of games.reverse()) {
      const response = await request.delete(`/api/v2/projects/${game.project_id}`);
      expect(response.status()).toBe(202);
      const task = await response
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      await expect
        .poll(
          async () =>
            (
              await (
                await request.get(`/api/v2/tasks/project-deletions/${task.task_id}`)
              )
                .json()
                .then((body) =>
                  body && typeof body === 'object' && 'data' in body ? body.data : body,
                )
            ).state,
        )
        .toBe('completed');
    }
  }
});
