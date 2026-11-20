import { test, expect } from './fixtures.js';
import { headers, events, probe } from './support.js';
import { resolve } from 'node:path';
test('actual game Quit returns to library and records one completed play', async ({
  page,
  request,
}) => {
  const game = await (
    await request.post('/api/v2/projects', {
      headers,
      data: { path: '/projects/Stream-Quit-Probe' },
    })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const baseline = events(resolve(probe, '../Stream-Quit-Probe')).length;
  const started = await (
    await request.post(`/api/v2/projects/${game.project_id}/start`, { headers, data: {} })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  try {
    await expect
      .poll(() =>
        events(resolve(probe, '../Stream-Quit-Probe'))
          .slice(baseline)
          .some((row) => row.event === 'main_menu'),
      )
      .toBe(true);
    await page.goto(`/?session=${started.session_id}`);
    await page.waitForFunction(
      () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
    );
    const box = (await page.locator('#videoStream').boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.keyboard.press('q');
    await expect(page).toHaveURL(/\/library\/$/, { timeout: 10000 });
    await expect(page.locator('#message')).toHaveText('游戏已退出');
    const state = await (
      await request.get('/api/v2/session', { headers })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(state.state).toBe('stopped');
    expect(state.reason).toBe('game_exit');
    const info = await (
      await request.get(`/api/v2/projects/${game.project_id}`, { headers })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(info.play_seconds).toBeGreaterThan(0);
    expect(
      (
        await (
          await request.get(`/api/v2/projects/${game.project_id}`, { headers })
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).play_seconds,
    ).toBe(info.play_seconds);
  } finally {
    await request.post('/api/v2/session/stop', { headers, data: {} });
  }
});
test('idle stream redirects but failed API request does not', async ({ page }) => {
  await page.route('**/api/v2/session', (r) => r.abort());
  await page.goto('/');
  await expect(page.getByRole('status')).toContainText('正在重试');
  await expect(page).toHaveURL(/\/$/);
  expect(new URL(page.url()).pathname).toBe('/');
  await page.unroute('**/api/v2/session');
  await page.reload();
  await expect(page).toHaveURL(/\/library\/$/);
});
test('replaced and failed sessions return with a reason', async ({ page }) => {
  for (const state of [
    { state: 'running', session_id: 'replacement' },
    { state: 'failed', exit_code: 3 },
  ]) {
    await page.route('**/api/v2/session', (r) => r.fulfill({ json: { data: state } }));
    await page.goto('/?session=old');
    await expect(page).toHaveURL(/\/library\/$/);
    await expect(page.locator('#message')).not.toBeEmpty();
    await page.unroute('**/api/v2/session');
  }
});

test('returning from background immediately rechecks the session', async ({ page }) => {
  let state: Record<string, unknown> = { state: 'running', session_id: 'b'.repeat(32) };
  await page.route('**/api/v2/session', (r) => r.fulfill({ json: { data: state } }));
  await page.goto('/?session=' + 'b'.repeat(32));
  await page.waitForFunction(
    () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
  );
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  state = { state: 'stopped' };
  await page.waitForTimeout(1100);
  expect(new URL(page.url()).pathname).toBe('/');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect(page).toHaveURL(/\/library\/$/, { timeout: 2000 });
});
