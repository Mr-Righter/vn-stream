import { mockEnvelope, apiData, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect } from '@playwright/test';
import { signIn } from './fixtures.js';
test('login password visibility is keyboard accessible and survives preference changes', async ({
  page,
}) => {
  await page.goto('/login');
  await page.locator('#username').fill('player');
  await page.locator('#password').fill('private-preview');
  const reveal = page.getByRole('button', { name: '显示密码', exact: true });
  await reveal.focus();
  await reveal.press('Enter');
  await expect(page.locator('#password')).toHaveAttribute('type', 'text');
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await page.locator('#theme-toggle').click();
  await expect(page.locator('#username')).toHaveValue('player');
  await expect(page.locator('#password')).toHaveValue('private-preview');
  await page.getByRole('button', { name: 'Hide password', exact: true }).click();
  await expect(page.locator('#password')).toHaveAttribute('type', 'password');
});
test('desktop details keep actions in content while phone keeps the start action visible', async ({
  page,
}) => {
  await signIn(page);
  await page.goto('about:blank');
  const game = renpyProjectFixture({
    project_id: 'b'.repeat(32),
    name: 'Layout fixture',
    path: '/projects/layout',
    engine_version: '8.3.2',
    release_version: '0.4',
    size_bytes: 4096,
    recommendation: {
      build_version: '8.3.2',
      kind: 'exact',
      runtime_id: 'renpy:8.3.2',
      display_name: null,
    },
  });
  await page.route('**/api/v2/**', (route) => {
    const path = new URL(route.request().url()).pathname.split('/api/v2/')[1];
    const payload: Record<string, unknown> = {
      projects: [game],

      session: { state: 'stopped' },
      'imports/active': null,
      'uploads/directories/active': null,
      engines: engineFixtures,
    };
    return route.fulfill({ json: mockEnvelope(route.request().url(), payload[path] ?? null, 200) });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/#projects/${game.project_id}`);
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeEnabled();
  await expect(page.locator('.bottom-bar')).toHaveCSS('position', 'static');
  await page.setViewportSize({ width: 390, height: 700 });
  await expect(page.locator('.bottom-bar')).toHaveCSS('position', 'fixed');
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeInViewport();
});
test('failed application chunks leave a localized error instead of a blank page', async ({
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
  await page.route('**/chunks/*.js*', (route) => route.abort());
  await page.goto('/login');
  await expect(page.getByRole('alert')).toContainText('Unable to load');
});
for (const width of [320, 390, 844, 1280]) {
  test(`shared header alignment and menu icons at ${width}px`, async ({ page }) => {
    await signIn(page);
    await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
    const header = page.getByRole('banner');
    const brand = header.getByRole('button', { name: 'VN Stream', exact: true });
    await expect(brand).toBeVisible();
    const logo = (await brand.locator('img').boundingBox())!;
    const wordmark = (await brand.locator('.vn-brand-name').boundingBox())!;
    expect(wordmark.x).toBeGreaterThan(logo.x + logo.width);
    expect(
      Math.abs(logo.y + logo.height / 2 - wordmark.y - wordmark.height / 2),
    ).toBeLessThanOrEqual(1);
    const languageLabel = header.locator('.vn-language-label');
    if (width <= 480) await expect(languageLabel).toBeHidden();
    else await expect(languageLabel).toBeVisible();
    for (const language of ['zh', 'en']) {
      for (const appearance of ['dark', 'light']) {
        await page.evaluate(
          ({ language, appearance }) => {
            window.dispatchEvent(
              new StorageEvent('storage', {
                key: 'vn-stream:preferences',
                newValue: JSON.stringify({ language, appearance }),
              }),
            );
          },
          { language, appearance },
        );
        const languageBox = (await page.locator('#language-toggle').boundingBox())!;
        const themeBox = (await page.locator('#theme-toggle').boundingBox())!;
        const menuBox = (await page.locator('#menu-toggle').boundingBox())!;
        for (const box of [languageBox, themeBox, menuBox]) {
          expect(box.width).toBeGreaterThanOrEqual(44);
          expect(box.height).toBeGreaterThanOrEqual(44);
          expect(
            Math.abs(box.y + box.height / 2 - themeBox.y - themeBox.height / 2),
          ).toBeLessThanOrEqual(1);
        }
        const icons = await page.locator('#language-toggle svg:visible').all();
        for (const icon of icons) {
          const box = (await icon.boundingBox())!;
          expect(
            Math.abs(box.y + box.height / 2 - languageBox.y - languageBox.height / 2),
          ).toBeLessThanOrEqual(1);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await page.locator('#menu-toggle').click();
        for (const id of ['add-game', 'refresh', 'logout'])
          await expect(
            page
              .getByRole('menuitem')
              .filter({ has: page.locator('#' + id) })
              .locator('svg'),
          ).toHaveCount(1);
        await page.keyboard.press('Escape');
      }
    }
  });
}
test('the mobile header stays at the top and its language menu works by keyboard', async ({
  page,
}) => {
  await signIn(page);
  await page.setViewportSize({ width: 390, height: 700 });
  const header = page.getByRole('banner');
  const language = header.getByRole('button', { name: '语言', exact: true });
  await expect(header).toHaveCSS('position', 'sticky');
  await page.addStyleTag({ content: 'main.vn-shell { min-height: 170vh; }' });
  await page.evaluate(() => window.scrollTo(0, 300));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(300);
  expect((await header.boundingBox())?.y).toBe(0);
  await language.focus();
  await language.press('Enter');
  const menu = page.getByRole('menu');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('menuitem').first()).toBeFocused();
  await expect(language).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(language).toBeFocused();
  await expect(language).toHaveAttribute('aria-expanded', 'false');
  expect(await page.evaluate(() => window.scrollY)).toBe(300);
  await language.press('Enter');
  const english = menu.getByRole('menuitem', { name: 'English', exact: true });
  await expect(menu.getByRole('menuitem').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(english).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('My games');
  await expect(language).toBeHidden();
  const theme = header.getByRole('button', { name: 'Dark mode', exact: true });
  await theme.press('Space');
  await expect(theme).toHaveAttribute('aria-pressed', 'false');
  await page.reload();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('My games');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
test('an in-flight upload keeps its XHR, selection and password through preferences and navigation', async ({
  page,
}) => {
  await signIn(page);
  await page.goto('about:blank');
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let creates = 0,
    chunks = 0,
    jobs = 0;
  await page.route('**/api/v2/uploads/archives', async (route) => {
    creates++;
    await route.fulfill({
      json: mockEnvelope(route.request().url(), { upload_id: 'held', offset_bytes: 0 }, 200),
    });
  });
  await page.route('**/api/v2/uploads/archives/held*', async (route) => {
    if (route.request().method() === 'PUT') {
      chunks++;
      await gate;
    }
    await route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { upload_id: 'held', offset_bytes: route.request().method() === 'PUT' ? 4 : 0 },
        200,
      ),
    });
  });
  await page.route('**/api/v2/imports', async (route) => {
    jobs++;
    expect(route.request().postDataJSON().password).toBe('keep-password');
    expect(route.request().headers()['accept-language']).toBe('en');
    await route.fulfill({
      status: 202,
      json: mockEnvelope(
        route.request().url(),
        {
          task_id: 'held-job',
          state: 'failed',
          phase: 'failed',
          upload_id: 'held',
          directory_upload_id: null,
          source_name: 'held.zip',
          error: 'Password required',
          completed_bytes: 0,
          total_bytes: null,
        },
        202,
      ),
    });
  });
  try {
    await page.goto('/#add');
    await page.locator('#archive-file').setInputFiles({
      name: 'held.zip',
      mimeType: 'application/zip',
      buffer: Buffer.from('test'),
    });
    await page.locator('#archive-password').fill('keep-password');
    await page.locator('#start-import').click();
    await expect.poll(() => chunks).toBe(1);
    const selectedInput = await page.locator('#archive-file').elementHandle();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '0ms');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '180ms');
    expect(await selectedInput!.evaluate((element) => element.isConnected)).toBe(true);
    await page.locator('#language-toggle').click();
    await page.locator('#language-en').click();
    await page.locator('#theme-toggle').click();
    await page.locator('#back').click();
    await page.locator('#menu-toggle').click();
    await page.locator('#add-game').click();
    await expect(page.locator('#archive-password')).toHaveValue('keep-password');
    expect(
      await page
        .locator('#archive-file')
        .evaluate((input: HTMLInputElement) => input.files?.[0]?.name),
    ).toBe('held.zip');
    expect(creates).toBe(1);
    expect(chunks).toBe(1);
    release();
    await expect.poll(() => jobs).toBe(1);
    await expect(page.locator('#start-import')).toHaveText('Retry import');
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
  }
});
test('failed stable entry also leaves a localized loading error', async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.setItem(
      'vn-stream:preferences',
      JSON.stringify({
        ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
        language: 'en',
      }),
    ),
  );
  await page.route('**/login.js', (route) => route.abort());
  await page.goto('/login');
  await expect(page.getByRole('alert')).toContainText('Unable to load');
});
test('anonymous login loads its own layout and theme icons before authentication', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/login');
  const header = page.getByRole('banner');
  const icon = header.locator('img');
  await expect(icon).toBeVisible();
  await expect
    .poll(() => icon.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBeGreaterThan(0);
  await expect(header.locator('#menu-toggle')).toHaveCount(0);
  await expect(page.locator('#language-toggle')).toBeVisible();
  const card = (await page.locator('.vn-login-card').boundingBox())!;
  expect(card.width).toBeLessThanOrEqual(400);
  const mask = await page
    .locator('.vn-theme-indicator')
    .evaluate((node) => getComputedStyle(node, '::after').maskImage);
  expect(mask).toContain('moon.svg');
  await expect(page.locator('#theme-toggle')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('#theme-toggle')).toHaveAttribute('aria-pressed', 'false');
  await expect
    .poll(() =>
      page
        .locator('.vn-theme-indicator')
        .evaluate((node) => getComputedStyle(node, '::after').maskImage),
    )
    .toContain('sun.svg');
});
