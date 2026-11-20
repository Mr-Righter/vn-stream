import { test, expect } from './fixtures.js';

test('game fits the visual viewport when browser chrome changes without a window resize', async ({
  page,
}) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await page.route('**/api/v2/session', (r) =>
    r.fulfill({ json: { state: 'running', session_id: 'viewport-test' } }),
  );
  await page.goto('/');
  await page.waitForFunction(
    () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
  );
  await page.evaluate(() => {
    Object.defineProperties(window.visualViewport!, {
      height: { configurable: true, value: 300 },
      offsetTop: { configurable: true, value: 20 },
    });
    window.visualViewport!.dispatchEvent(new Event('resize'));
  });
  await expect
    .poll(async () => {
      const box = await page.locator('#videoStream').boundingBox();
      return !!box && box.y >= 19 && box.y + box.height <= 321;
    })
    .toBe(true);
  const video = (await page.locator('#videoStream').boundingBox())!;
  const input = (await page.locator('#overlayInput').boundingBox())!;
  expect(video.width / video.height).toBeCloseTo(16 / 9, 2);
  expect(input.x).toBeCloseTo(video.x, 0);
  expect(input.y).toBeCloseTo(video.y, 0);
  expect(input.width).toBeCloseTo(video.width, 0);
  expect(input.height).toBeCloseTo(video.height, 0);
  const controls = (await page.locator('#vn-game-controls').boundingBox())!;
  expect(controls.y + controls.height).toBeLessThanOrEqual(321);
  await page.getByRole('button', { name: '游戏设置', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出游戏', exact: true })).toBeInViewport();
  const panel = (await page.locator('#game-settings-toggle').boundingBox())!;
  expect(panel.y).toBeGreaterThanOrEqual(20);
});
