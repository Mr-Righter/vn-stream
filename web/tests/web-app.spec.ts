import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
for (const path of ['/login', '/']) {
  test(`installation metadata agrees with the canonical manifest on ${path}`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute(
      'href',
      '/manifest.webmanifest',
    );
    await expect(page.locator('meta[name="apple-mobile-web-app-capable"]')).toHaveAttribute(
      'content',
      'yes',
    );
    const manifest = apiData(await (await page.request.get('/manifest.webmanifest')).json());
    expect(manifest).toMatchObject({
      name: 'VN Stream',
      short_name: 'VN Stream',
      id: '/',
      scope: '/',
      start_url: '/',
      display: 'standalone',
    });
    expect(
      new URL('/?session=abc&display_revision=2', page.url()).pathname.startsWith(manifest.scope),
    ).toBe(true);
  });
}
test('keyboard remains invisible and can be focused again after collapsing shortcuts', async ({
  page,
}) => {
  await page.route('**/invisible-keyboard?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<html><body><input id="keyboard-input-assist"><div id="overlayInput"></div><script type="module" src="/game-controls.js"></script></body></html>',
    }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { state: 'running', session_id: 'a'.repeat(32) },
        200,
      ),
    }),
  );
  await page.routeWebSocket('**/api/v2/session/control', (ws) =>
    ws.onMessage((raw) => {
      if (JSON.parse(String(raw)).type === 'hello')
        ws.send(
          JSON.stringify({
            type: 'status',
            controller: true,
            ready: true,
            epoch: 'e',
            skip: false,
            supported: ['menu', 'quit'],
            available: ['menu', 'quit'],
          }),
        );
    }),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/invisible-keyboard?session=' + 'a'.repeat(32));
  const input = page.locator('#keyboard-input-assist');
  for (let i = 0; i < 2; i++) {
    await page.getByRole('button', { name: '键盘', exact: true }).click();
    await expect(input).toBeFocused();
    const geometry = await input.evaluate((el) => {
      const s = getComputedStyle(el),
        r = el.getBoundingClientRect();
      return {
        opacity: s.opacity,
        display: s.display,
        visibility: s.visibility,
        width: r.width,
        height: r.height,
        pointerEvents: s.pointerEvents,
        placeholder: el.getAttribute('placeholder'),
        inside: !!el.closest('.allow-native-input'),
      };
    });
    expect(geometry.opacity).toBe('0');
    expect(geometry.width).toBeLessThanOrEqual(1);
    expect(geometry.height).toBeLessThanOrEqual(1);
    expect(geometry.display).not.toBe('none');
    expect(geometry.visibility).toBe('visible');
    expect(geometry.pointerEvents).toBe('none');
    expect(geometry.placeholder ?? '').toBe('');
    expect(geometry.inside).toBe(false);
    await page.getByRole('button', { name: '收起快捷栏', exact: true }).click();
    await expect(input).not.toBeFocused();
    await page.getByRole('button', { name: '游戏设置', exact: true }).click();
    await page.locator('.shortcut-switch').getByRole('switch').click();
    await page.keyboard.press('Escape');
  }
});
