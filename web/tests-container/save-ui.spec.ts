import { readFile } from 'node:fs/promises';
import { test, expect } from './fixtures.js';

test('compact save UI preserves real drafts, backup downloads and confirmed restoration', async ({
  page,
  request,
}) => {
  const fixture = JSON.parse(await readFile('../var/save-ui-work/fixture.json', 'utf8'));
  const response = await request.post('/api/v2/projects', {
    data: { path: '/projects/' + fixture.folder },
  });
  expect(response.status()).toBe(201);
  const game = await response
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const prefix = `/api/v2/projects/${game.project_id}`;
  try {
    const listing = await (
      await request.get(`${prefix}/saves`)
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    const slot = listing.saves.find((item: { name: string }) => item.name === '1-1.save');
    const target = `${prefix}/saves/${slot.id}`;
    const original = await (await request.get(`${target}/content`)).body();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/library/#projects/${game.project_id}/saves/item/${slot.id}`);
    const flag = page.getByRole('switch', { name: 'store.flag', exact: true });
    await expect(flag).toBeEnabled();
    await flag.click();
    for (let i = 0; i < 2; i++) {
      await page.getByRole('button', { name: '备份当前存档', exact: true }).click();
      await expect
        .poll(
          async () =>
            (
              await (
                await request.get(`${target}/backups`)
              )
                .json()
                .then((body) =>
                  body && typeof body === 'object' && 'data' in body ? body.data : body,
                )
            ).filter((b: { kind: string }) => b.kind === 'manual').length,
        )
        .toBe(i + 1);
      await expect(flag).toBeChecked();
    }
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await expect(page.getByText('1 pending changes')).toBeVisible();
    const history = page.getByRole('list', { name: 'Backup history', exact: true });
    await expect(history.getByRole('listitem').filter({ hasText: 'Manual backup' })).toHaveCount(2);
    for (const width of [320, 390, 844, 1280]) {
      await page.setViewportSize({ width, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#theme-toggle').click();
    await page.screenshot({
      path: '../var/save-ui-work/container-save-detail.png',
      fullPage: true,
    });
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('0 pending changes')).toBeVisible();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`${target}/variables`)
            )
              .json()
              .then((body) =>
                body && typeof body === 'object' && 'data' in body ? body.data : body,
              )
          ).fields.find((f: { name: string }) => f.name === 'store.flag').value,
      )
      .toBe(true);
    const manual = history.getByRole('listitem').filter({ hasText: 'Manual backup' }).first();
    const download = await request.get(
      (await manual.getByRole('link', { name: 'Download', exact: true }).getAttribute('href'))!,
    );
    expect(download.status()).toBe(200);
    expect((await download.body()).equals(original)).toBe(true);
    await manual.getByRole('button', { name: 'Restore this backup', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(flag).toBeChecked();
    await manual.getByRole('button', { name: 'Restore this backup', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'OK', exact: true }).click();
    await expect(flag).not.toBeChecked();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get(`${target}/variables`)
            )
              .json()
              .then((body) =>
                body && typeof body === 'object' && 'data' in body ? body.data : body,
              )
          ).fields.find((f: { name: string }) => f.name === 'store.flag').value,
      )
      .toBe(false);
  } finally {
    await page.goto('/library/');
    const response = await request.delete(prefix);
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
});
