import { test as base, expect, type Page } from '@playwright/test';
import { credentials, headers } from './support.js';
export async function signIn(page: Page) {
  await expect
    .poll(
      async () => {
        try {
          return (await page.request.get('/api/v2/auth/session')).status();
        } catch {
          return 0;
        }
      },
      { timeout: 30000 },
    )
    .toBe(401);
  await page.goto('/library/login');
  await page.locator('#username').fill(credentials.username);
  await page.locator('#password').fill(credentials.password);
  await page.locator('#login-submit').click();
  await expect(page.locator('h1')).toHaveText('我的游戏');
}
export const test = base.extend({
  page: async ({ page }, use) => {
    await signIn(page);
    await page.goto('about:blank');
    await use(page);
  },
  request: async ({ page, playwright, baseURL }, use) => {
    headers['X-CSRF-Token'] = (await page.context().cookies()).find(
      (c) => c.name === 'vn_stream_csrf',
    )!.value;
    await page.context().setExtraHTTPHeaders(headers);
    const api = await playwright.request.newContext({
      baseURL,
      storageState: await page.context().storageState(),
      extraHTTPHeaders: {
        'X-CSRF-Token': (await page.context().cookies()).find((c) => c.name === 'vn_stream_csrf')!
          .value,
      },
    });
    await use(api);
    await api.dispose();
  },
});
export { expect };
export type * from '@playwright/test';
