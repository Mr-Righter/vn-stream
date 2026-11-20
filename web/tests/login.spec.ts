import { test, expect } from '@playwright/test';
import { setAppearance } from './fixtures.js';
test('real login persists in context, returns safely and signs out without challenge', async ({
  page,
  context,
}) => {
  await page.goto('/#add');
  await expect(page.getByRole('banner').locator('.vn-brand')).toHaveText('VN Stream');
  await expect(page).toHaveURL(/\/login\?/);
  await expect(page.locator('#remember')).toBeChecked();
  await expect(page.locator('#username')).toHaveAttribute('autocomplete', 'username');
  await expect(page.locator('#password')).toHaveAttribute('autocomplete', 'current-password');
  await page.locator('#username').fill('player');
  await page.locator('#password').fill('wrong');
  await page.locator('#login-submit').click();
  await expect(page.locator('#login-error')).toHaveText('账号或密码不正确');
  await page.locator('#password').fill('browser-test-only');
  await page.locator('#login-submit').click();
  await expect(page.locator('h1')).toHaveText('添加游戏');
  await expect(page).toHaveURL(/\/#add$/);
  const cookie = (await context.cookies()).find((c) => c.name === 'vn_stream_session')!;
  expect(cookie.httpOnly).toBeTruthy();
  expect(cookie.sameSite).toBe('Lax');
  expect(cookie.expires - Date.now() / 1000).toBeGreaterThan(29 * 86400);
  await page.reload();
  await expect(page.locator('h1')).toHaveText('添加游戏');
  await page.getByRole('button', { name: '管理菜单' }).click();
  await page.locator('#logout').click();
  await expect(page.locator('#login-form')).toBeVisible();
  const response = await context.request.get('/api/v2/session');
  expect(response.status()).toBe(401);
  expect(response.headers()['www-authenticate']).toBeUndefined();
});
test('unchecked login is a browser session and external next cannot redirect', async ({
  page,
  context,
}) => {
  await page.goto('/login?next=https://foreign.invalid');
  await page.locator('#username').fill('player');
  await page.locator('#password').fill('browser-test-only');
  await page.locator('#remember').uncheck();
  await page.locator('#login-submit').click();
  await expect(page.locator('h1')).toHaveText('我的游戏');
  expect((await context.cookies()).find((c) => c.name === 'vn_stream_session')!.expires).toBe(-1);
});
test('mobile login themes and language fit 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/login');
  await page.locator('#language-toggle').click();
  await page.locator('#language-en').click();
  await setAppearance(page, 'light');
  await expect(page.locator('h1')).toHaveText('Welcome back');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
});
test('a blocked login script cannot submit a password in the URL', async ({ page }) => {
  await page.route('**/login.js', (route) => route.abort());
  await page.goto('/login');
  await expect(page.locator('#username')).toBeDisabled();
  await page.locator('#username').evaluate((node: HTMLInputElement) => {
    node.value = 'player';
  });
  await page.locator('#password').evaluate((node: HTMLInputElement) => {
    node.value = 'fake-password-never-in-url';
  });
  const navigation = page.waitForRequest((request) => request.isNavigationRequest());
  await page.locator('#login-form').evaluate((form: HTMLFormElement) => form.submit());
  const request = await navigation;
  expect(request.method()).toBe('POST');
  expect(new URL(request.url()).pathname).toBe('/api/v2/auth/login');
  expect(request.url()).not.toContain('fake-password-never-in-url');
});
