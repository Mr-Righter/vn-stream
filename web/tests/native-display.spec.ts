import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
test('late display upgrade reloads media once within the same game session', async ({ page }) => {
  let revision = 1;
  let navigations = 0;
  await page.route('**/player-native?*', async (route) => {
    navigations++;
    await route.fulfill({
      contentType: 'text/html',
      body: '<html><body><script type="module" src="/game-controls.js"></script></body></html>',
    });
  });
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          state: 'running',
          session_id: 'native-test',
          display: {
            revision,
            notice: 'temporary 1080p',
            messages: {
              notice: {
                code: 'errors.nativeCanvasUnavailableTemporarilyUsing19201080ItMay',
                params: {},
              },
            },
            state: revision === 1 ? 'fallback' : 'native',
          },
        },
        200,
      ),
    }),
  );
  await page.goto('/player-native?session=native-test');
  await expect(
    page.getByText('无法及时确定原生画布，已暂用 1920×1080；稍后可自动恢复。'),
  ).toBeVisible();
  revision = 2;
  await expect.poll(() => navigations).toBe(2);
  await page.waitForTimeout(2200);
  expect(navigations).toBe(2);
  await expect(
    page.getByText('无法及时确定原生画布，已暂用 1920×1080；稍后可自动恢复。'),
  ).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get('session')).toBe('native-test');
});
test('server-owned capture fits media and input locally after viewport rotation', async ({
  page,
}) => {
  await page.route('**/player-native?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<html><body><div class="video-container" style="position:fixed;width:100vw;height:100vh"><canvas id="videoCanvas" width="808" height="452"></canvas><div id="overlayInput"></div></div><script>window.enable_resize=false; window.fitCalls=0; window.webrtcInput={resize(){window.fitCalls++}}</script><script type="module" src="/game-controls.js"></script></body></html>`,
    }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(route.request().url(), { state: 'running', session_id: 'fit-test' }, 200),
    }),
  );
  await page.goto('/player-native?session=fit-test');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(async () => (await page.locator('#videoCanvas').boundingBox())?.width)
    .toBe(390);
  const video = (await page.locator('#videoCanvas').boundingBox())!;
  const input = (await page.locator('#overlayInput').boundingBox())!;
  expect(video.height).toBeCloseTo((390 * 452) / 808, 0);
  expect(input).toEqual(video);
  expect(await page.evaluate(() => (window as any).fitCalls)).toBeGreaterThan(0);
});
test('first session poll already upgraded reconnects an older active capture once', async ({
  page,
}) => {
  let navigations = 0;
  await page.route('**/player-native?*', (route) => {
    navigations++;
    return route.fulfill({
      contentType: 'text/html',
      body: `<html><body><canvas id="videoCanvas" width="${navigations === 1 ? 1920 : 808}" height="${navigations === 1 ? 1080 : 452}"></canvas><script type="module" src="/game-controls.js"></script></body></html>`,
    });
  });
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          state: 'running',
          session_id: 'first-native',
          display: { state: 'native', revision: 2, capture: [808, 452] },
        },
        200,
      ),
    }),
  );
  await page.goto('/player-native?session=first-native');
  await expect.poll(() => navigations).toBe(2);
  await page.waitForTimeout(2100);
  expect(navigations).toBe(2);
});
test('English acknowledgement failure does not mislabel native 1080p as fallback', async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        language: 'en',
      }),
    ),
  );
  await page.route('**/player-native?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<html><body><script type="module" src="/game-controls.js"></script></body></html>',
    }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          state: 'running',
          session_id: 'ack-native',
          display: {
            state: 'native',
            revision: 1,
            capture: [1920, 1080],
            diagnostic: 'ack_write_failed',
            notice: '显示握手未完成',
          },
        },
        200,
      ),
    }),
  );
  await page.goto('/player-native?session=ack-native');
  await expect(page.getByRole('status')).toContainText('Display handshake');
  await expect(page.getByRole('status')).not.toContainText('temporarily');
});
for (const deniedStorage of [false, true]) {
  test(`first poll revision 2 reconnects input with matching media and storage denied ${deniedStorage}`, async ({
    page,
  }) => {
    if (deniedStorage)
      await page.addInitScript(() => {
        Storage.prototype.getItem = () => {
          throw new Error('denied');
        };
        Storage.prototype.setItem = () => {
          throw new Error('denied');
        };
      });
    let navigations = 0;
    await page.route('**/player-native?*', (route) => {
      navigations++;
      return route.fulfill({
        contentType: 'text/html',
        body: '<html><body><canvas id="videoCanvas" width="1024" height="768"></canvas><script type="module" src="/game-controls.js"></script></body></html>',
      });
    });
    await page.route('**/api/v2/session', (route) =>
      route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            state: 'running',
            session_id: 'native-frame-stale-input',
            display: { state: 'native', revision: 2, capture: [1024, 768] },
          },
          200,
        ),
      }),
    );
    await page.goto('/player-native?session=native-frame-stale-input');
    await expect.poll(() => navigations).toBe(2);
    await page.waitForTimeout(2200);
    expect(navigations).toBe(2);
  });
}
test('matching video with stale hidden input canvas reconnects even at revision 1', async ({
  page,
}) => {
  let navigations = 0;
  await page.route('**/player-native?*', (route) => {
    navigations++;
    return route.fulfill({
      contentType: 'text/html',
      body: `<html><body><video id="videoStream"></video><canvas id="videoCanvas" style="display:none" width="${navigations === 1 ? 1920 : 1024}" height="${navigations === 1 ? 1080 : 768}"></canvas><script>Object.defineProperties(document.getElementById('videoStream'),{videoWidth:{value:1024},videoHeight:{value:768}})</script><script type="module" src="/game-controls.js"></script></body></html>`,
    });
  });
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        {
          state: 'running',
          session_id: 'hidden-input-stale',
          display: { state: 'native', revision: 1, capture: [1024, 768] },
        },
        200,
      ),
    }),
  );
  await page.goto('/player-native?session=hidden-input-stale');
  await expect.poll(() => navigations).toBe(2);
  await page.waitForTimeout(2200);
  expect(navigations).toBe(2);
});
