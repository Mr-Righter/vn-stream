import { mockEnvelope, apiData, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect } from '@playwright/test';
import { setAppearance } from './fixtures.js';
import { writeFile } from 'node:fs/promises';
const ratio = (a: number[], b: number[]) => {
  const lum = (rgb: number[]) =>
    rgb
      .map((v) => {
        const x = v / 255;
        return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
      })
      .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  return (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
};
for (const appearance of ['dark', 'light']) {
  test(`semantic theme, font fallback and reduced motion in ${appearance}`, async ({ page }) => {
    await page.addInitScript(
      (value) =>
        localStorage.setItem(
          'vn-stream:preferences',
          JSON.stringify({
            ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
            appearance: value,
          }),
        ),
      appearance,
    );
    await page.goto('/login');
    await expect(page.locator('#username')).toBeEnabled();
    await page.locator('#username').fill('retained-user');
    await page.locator('#password').fill('retained-password');
    const tokens = await page.locator('.vn-app').evaluate((element) => {
      const s = getComputedStyle(element);
      const colors = Object.fromEntries(
        ['bg', 'surface', 'text', 'muted', 'border', 'focus', 'accent'].map((name) => {
          const probe = document.createElement('span');
          probe.style.color = s.getPropertyValue(`--vn-${name}`);
          element.append(probe);
          const rgb = getComputedStyle(probe)
            .color.match(/[\d.]+/g)!
            .slice(0, 3)
            .map(Number);
          probe.remove();
          return [name, rgb];
        }),
      );
      return { colors, font: s.fontFamily, size: s.fontSize };
    });
    expect(tokens.colors.accent).toEqual([37, 99, 235]);
    expect(tokens.font).toContain('Noto Sans SC Variable');
    expect(tokens.size).toBe('16px');
    for (const surface of ['bg', 'surface']) {
      for (const text of ['text', 'muted'])
        expect(ratio(tokens.colors[text], tokens.colors[surface])).toBeGreaterThanOrEqual(4.5);
      for (const control of ['border', 'focus'])
        expect(ratio(tokens.colors[control], tokens.colors[surface])).toBeGreaterThanOrEqual(3);
    }
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '0ms');
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await page.locator('#theme-toggle').click();
    await expect(page.locator('#username')).toHaveValue('retained-user');
    await expect(page.locator('#password')).toHaveValue('retained-password');
    await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '0ms');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '180ms');
  });
}
for (const width of [320, 390, 844, 1280]) {
  test(`local font swaps safely and text remains usable at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: width === 844 ? 390 : 900 });
    const fonts: {
      url: string;
      bytes: number;
    }[] = [];
    page.on('response', async (response) => {
      if (response.url().endsWith('.woff2'))
        fonts.push({
          url: new URL(response.url()).pathname,
          bytes: (await response.body()).length,
        });
    });
    await page.route('**/*.woff2', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 800));
      await route.continue();
    });
    await page.goto('/login', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#username')).toBeEnabled();
    const before = (await page.locator('#username').boundingBox())!;
    const titleBefore = (await page.locator('h1').boundingBox())!;
    await page.locator('#username').fill('font-swap-keeps-input');
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator('#username')).toHaveValue('font-swap-keeps-input');
    const after = (await page.locator('#username').boundingBox())!;
    expect(Math.abs(before.width - after.width)).toBeLessThanOrEqual(1);
    expect(
      Math.abs(titleBefore.height - (await page.locator('h1').boundingBox())!.height),
    ).toBeLessThanOrEqual(1);
    expect(fonts.length).toBeGreaterThan(0);
    expect(fonts.length).toBeLessThan(101);
    await testInfo.attach('font-load.json', {
      body: JSON.stringify(
        { width, subsets: fonts.length, bytes: fonts.reduce((sum, f) => sum + f.bytes, 0), fonts },
        null,
        2,
      ),
      contentType: 'application/json',
    });
    for (const language of ['zh', 'en']) {
      for (const appearance of ['dark', 'light']) {
        await page.evaluate(
          ({ language, appearance }) => {
            window.dispatchEvent(
              new StorageEvent('storage', {
                key: 'vn-stream:preferences',
                newValue: JSON.stringify({
                  ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
                  language: language,
                }),
              }),
            );
            window.dispatchEvent(
              new StorageEvent('storage', {
                key: 'vn-stream:preferences',
                newValue: JSON.stringify({
                  ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
                  appearance: appearance,
                }),
              }),
            );
          },
          { language, appearance },
        );
        await expect(page.locator('.vn-app')).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
      }
    }
    await expect(page.locator('.vn-app')).toHaveCSS('background-color', 'rgb(245, 247, 251)');
    await expect(page.locator('#username')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
    await expect(page.locator('#login-submit')).toHaveCSS('background-color', 'rgb(37, 99, 235)');
    await page.screenshot({ path: `../var/visual-polish-work/login-${width}.png`, fullPage: true });
    await writeFile(
      `../var/visual-polish-work/font-load-${width}.json`,
      JSON.stringify(
        { subsets: fonts.length, bytes: fonts.reduce((sum, f) => sum + f.bytes, 0), fonts },
        null,
        2,
      ),
    );
    await page.addStyleTag({
      content:
        '.vn-app { font-size:32px } .vn-app .ant-typography,.vn-app label,.vn-app button,.vn-app input { font-size:inherit }',
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await expect(page.locator('#login-submit')).toBeVisible();
    await page.locator('#username').focus();
    await page.keyboard.press('Tab');
    await expect(page.locator('#password')).toBeFocused();
  });
}
test('font failure keeps login usable and bootstrap follows saved theme without JS', async ({
  page,
}) => {
  await page.addInitScript(() =>
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        appearance: 'light',
      }),
    ),
  );
  await page.route('**/*.woff2', (route) => route.abort());
  await page.goto('/login');
  await page.locator('#username').fill('fallback-works');
  await page.locator('#password').fill('fallback-password');
  await expect(page.locator('#login-submit')).toBeEnabled();
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#F5F7FB');
  await page.route('**/chunks/*.js*', (route) => route.abort());
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('加载');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(245, 247, 251)');
});
test('initial reduced motion can change without replacing the login form', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/login');
  await page.locator('#username').fill('reduced-at-launch');
  const handle = await page.locator('#username').elementHandle();
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '180ms');
  expect(await handle!.evaluate((element) => element.isConnected)).toBe(true);
  await expect(page.locator('#username')).toHaveValue('reduced-at-launch');
});
for (const appearance of ['dark', 'light']) {
  test(`actual primary and danger buttons retain readable contrast in ${appearance}`, async ({
    page,
  }) => {
    const { signIn } = await import('./fixtures.js');
    await signIn(page);
    await page.goto('about:blank');
    const game = renpyProjectFixture({
      project_id: 'e'.repeat(32),
      name: 'Contrast fixture',
      path: '/projects/contrast-fixture',
      engine_version: '8.3.2',
      release_version: '1.0',
      size_bytes: 1024,
      recommendation: {
        build_version: '8.3.2',
        kind: 'exact',
        runtime_id: 'renpy:8.3.2',
        display_name: null,
      },
    });
    await page.route('**/api/v2/**', (route) => {
      const key = new URL(route.request().url()).pathname.split('/api/v2/')[1];
      const responses: Record<string, unknown> = {
        projects: [game],

        session: { state: 'stopped' },
        'imports/active': null,
        'uploads/directories/active': null,
        engines: engineFixtures,
      };
      return route.fulfill({
        json: mockEnvelope(route.request().url(), responses[key] ?? null, 200),
      });
    });
    await page.goto(`/#projects/${game.project_id}`);
    await setAppearance(page, appearance);
    const start = page.getByRole('button', { name: '开始', exact: true });
    await expect(start).toBeEnabled();
    await expect(start).toHaveCSS('background-color', 'rgb(37, 99, 235)');
    async function buttonRatio(selector: string) {
      const colors = await page.locator(selector).evaluate((element) => {
        const style = getComputedStyle(element);
        return [style.color, style.backgroundColor].map((color) =>
          color
            .match(/[\d.]+/g)!
            .slice(0, 3)
            .map(Number),
        );
      });
      return ratio(colors[0], colors[1]);
    }
    expect(await buttonRatio('.bottom-bar .ant-btn-primary')).toBeGreaterThanOrEqual(4.5);
    await page.getByRole('button', { name: '删除游戏', exact: true }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(await page.getByRole('dialog').evaluate((element) => !!element.closest('.vn-app'))).toBe(
      true,
    );
    expect(
      await buttonRatio('.ant-modal .ant-btn-dangerous.ant-btn-primary'),
    ).toBeGreaterThanOrEqual(4.5);
    await page.setViewportSize({ width: 320, height: 700 });
    await page.evaluate(() =>
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: 'vn-stream:preferences',
          newValue: JSON.stringify({
            ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
            language: 'en',
          }),
        }),
      ),
    );
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  });
}
test('light bootstrap respects reduced motion before React is available', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(() =>
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        appearance: 'light',
      }),
    ),
  );
  await page.route('**/chunks/*.js*', (route) => route.abort());
  await page.goto('/login');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('html')).toHaveCSS('--vn-motion-slow', '0ms');
  await expect(page.locator('.vn-shell')).toHaveCSS('animation-duration', '0s');
});
