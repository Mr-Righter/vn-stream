import { test, expect } from './fixtures.js';
import { readFile } from 'node:fs/promises';

test('real signed save has repeatable manual history and preserves unsaved draft', async ({
  page,
  request,
}) => {
  const folder = (await readFile('../var/save-polish-work/content-fixture-name', 'utf8')).trim();
  const imported = await request.post('/api/v2/projects', {
    data: { path: '/projects/' + folder },
  });
  expect(imported.status()).toBe(201);
  const game = await imported
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const prefix = '/api/v2/projects/' + game.project_id;
  try {
    const listing = await (
      await request.get(prefix + '/saves')
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    const slot = listing.saves[0];
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/library/#projects/' + game.project_id + '/saves/item/' + slot.id);
    await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeEnabled();
    await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
    for (let i = 0; i < 2; i++) {
      await page.getByRole('button', { name: '备份当前存档', exact: true }).click();
      await expect(page.getByRole('switch', { name: 'store.flag', exact: true })).toBeChecked();
      await expect
        .poll(
          async () =>
            (
              await (
                await request.get(prefix + '/saves/' + slot.id + '/backups')
              )
                .json()
                .then((body) =>
                  body && typeof body === 'object' && 'data' in body ? body.data : body,
                )
            ).filter((item: any) => item.kind === 'manual').length,
        )
        .toBe(i + 1);
    }
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await expect(page.getByText('1 pending changes')).toBeVisible();
    await expect(page.getByText('Manual backup', { exact: true })).toHaveCount(2);
    await page.setViewportSize({ width: 320, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    const history = page.locator('.vn-backup-list');
    const historyBox = await history.boundingBox();
    for (const tag of await history.locator('.ant-tag').all()) {
      const box = await tag.boundingBox();
      expect(box!.x + box!.width).toBeLessThanOrEqual(historyBox!.x + historyBox!.width);
    }
    await page.screenshot({
      path: '../var/save-polish-work/container-save-320.png',
      fullPage: true,
    });
  } finally {
    // Discard only this self-authored fixture's unsaved browser draft.
    await page.goto('/library/');
    const deletion = await request.delete(prefix);
    expect(deletion.status()).toBe(202);
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(
                '/api/v2/tasks/project-deletions/' +
                  (
                    await deletion
                      .json()
                      .then((body) =>
                        body && typeof body === 'object' && 'data' in body ? body.data : body,
                      )
                  ).task_id,
              )
            )
              .json()
              .then((body) =>
                body && typeof body === 'object' && 'data' in body ? body.data : body,
              )
          ).state,
      )
      .toBe('completed');
  }
});
