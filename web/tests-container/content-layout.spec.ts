import { test, expect } from './fixtures.js';
import { readFile } from 'node:fs/promises';

test('real compact media and save detail pages use cached posters and preserve drafts', async ({
  page,
  request,
}) => {
  const folder =
    process.env.VN_STREAM_CONTENT_FIXTURE ??
    (await readFile('../var/content-layout-work/content-fixture-name', 'utf8')).trim();
  const imported = await request.post('/api/v2/projects', {
    data: { path: '/projects/' + folder },
  });
  expect(imported.ok()).toBeTruthy();
  const game = await imported
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body)),
    prefix = '/api/v2/projects/' + game.project_id;
  try {
    const job = await (
      await request.post(prefix + '/assets/extract', {
        data: { request_id: 'layout-' + game.project_id },
      })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get('/api/v2/tasks/assets/' + job.task_id)
            )
              .json()
              .then((body) =>
                body && typeof body === 'object' && 'data' in body ? body.data : body,
              )
          ).state,
      )
      .toBe('completed');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/library/#projects/' + game.project_id + '/assets');
    await page.getByTestId('asset-category-video').click();
    const row = page.locator('.vn-content-row').filter({ hasText: 'clip.webm' });
    await expect(row.locator('img')).toBeVisible();
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.screenshot({
      path: '../var/content-layout-work/container-video-list-light-390.png',
      fullPage: true,
    });
    await row.click();
    await expect(page.locator('.hero')).toBeHidden();
    await expect.poll(() => page.locator('video').getAttribute('poster')).toMatch(/^blob:/);
    await expect
      .poll(() => page.locator('video').evaluate((n: HTMLVideoElement) => n.readyState))
      .toBeGreaterThanOrEqual(2);
    await page.locator('video').evaluate((n: HTMLVideoElement) => {
      (window as any).oldVideo = n;
      return n.play();
    });
    await page.screenshot({
      path: '../var/content-layout-work/container-video-detail-light-390.png',
      fullPage: true,
    });
    await page.locator('#back').click();
    await expect.poll(() => page.evaluate(() => (window as any).oldVideo.paused)).toBeTruthy();
    await page.goto('/library/#projects/' + game.project_id + '/saves');
    const slot = page.locator('[data-testid^="save-row-"]').first();
    await expect(slot.locator('img')).toBeVisible();
    await expect(slot.getByRole('button')).toHaveCount(0);
    await slot.click();
    await page.getByRole('switch', { name: 'store.flag', exact: true }).click();
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await expect(page.getByText('1 pending changes')).toBeVisible();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    await expect(page.getByText('0 pending changes')).toBeVisible();
    await page.setViewportSize({ width: 320, height: 844 });
    const actions = page.locator('.vn-save-editor-actions');
    await actions.scrollIntoViewIfNeeded();
    const buttons = await actions.getByRole('button').evaluateAll((nodes) =>
      nodes.map((node) => {
        const r = node.getBoundingClientRect();
        return { y: r.y, height: r.height };
      }),
    );
    expect(Math.abs(buttons[0].y - buttons[1].y)).toBeLessThanOrEqual(1);
    expect(buttons.every((button) => button.height >= 44)).toBeTruthy();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    ).toBeTruthy();
    await page.screenshot({
      path: '../var/content-layout-work/container-save-detail-390.png',
      fullPage: true,
    });
  } finally {
    await page.goto('/library/');
    const result = await request.delete(prefix);
    expect(result.status()).toBe(202);
    const deletion = await result
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get('/api/v2/tasks/project-deletions/' + deletion.task_id)
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
