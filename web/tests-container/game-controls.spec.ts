import { test, expect } from './fixtures.js';
import { events } from './support.js';

async function startProbe(request: any) {
  const game = await (
    await request.post('/api/v2/projects', { data: { path: '/projects/Stream-Probe' } })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const started = await request.post(`/api/v2/projects/${game.project_id}/start`, { data: {} });
  expect(started.ok()).toBe(true);
  return started
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
}
async function ready(page: any) {
  await page.waitForFunction(
    () => ((document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth ?? 0) > 0,
  );
}

test.describe('mobile controls', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  test('native start tap, shortcut toggle and quit work with touch', async ({ page, request }) => {
    const before = events().length;
    const session = await startProbe(request);
    try {
      await page.goto('/?session=' + session.session_id);
      await ready(page);
      await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
      const box = await page.locator('#videoStream').boundingBox();
      await page.touchscreen.tap(
        box!.x + (box!.width * 150) / 640,
        box!.y + (box!.height * 115) / 360,
      );
      await expect
        .poll(() =>
          events()
            .slice(before)
            .some((r) => r.event === 'ready'),
        )
        .toBe(true);
      await page.getByRole('button', { name: '收起快捷栏', exact: true }).tap();
      await expect(page.locator('#game-shortcuts')).toBeHidden();
      await page.getByRole('button', { name: '游戏设置', exact: true }).tap();
      await page.locator('.shortcut-switch').getByRole('switch').tap();
      await expect(page.locator('#game-shortcuts')).toBeVisible();
      await page.screenshot({ path: '../var/antd-work/mobile-player.png' });
      await page.getByRole('button', { name: '退出游戏', exact: true }).tap();
      await expect(page).toHaveURL(/\/library\/$/, { timeout: 10000 });
    } finally {
      await request.post('/api/v2/session/stop');
    }
  });
});

test('gear and shortcut toolbar remain available in fullscreen', async ({ page, request }) => {
  const session = await startProbe(request);
  try {
    await page.goto('/?session=' + session.session_id);
    await ready(page);
    await page.evaluate(() =>
      document.querySelector<HTMLElement>('.video-container')!.requestFullscreen(),
    );
    await expect
      .poll(() =>
        page.evaluate(() =>
          document.fullscreenElement?.contains(document.getElementById('vn-game-controls')),
        ),
      )
      .toBe(true);
    await page.getByRole('button', { name: '游戏设置', exact: true }).click();
    await expect(page.getByRole('button', { name: '退出游戏', exact: true })).toBeVisible();
    await page.evaluate(() => document.exitFullscreen());
  } finally {
    await request.post('/api/v2/session/stop');
  }
});
