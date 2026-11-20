import { test as base, expect, type Page } from '@playwright/test';
export async function signIn(page: Page) {
  await page.goto('/login');
  await page.locator('#username').fill('player');
  await page.locator('#password').fill('browser-test-only');
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
    const csrf = (await page.context().cookies()).find((c) => c.name === 'vn_stream_csrf')!.value;
    await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': csrf });
    const api = await playwright.request.newContext({
      baseURL,
      ignoreHTTPSErrors: true,
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
export async function setAppearance(page: Page, value: string) {
  if ((await page.locator('html').getAttribute('data-theme')) !== value)
    await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', value);
  await page.evaluate(() =>
    Promise.allSettled(
      document
        .getAnimations()
        .filter((animation) => animation instanceof CSSTransition)
        .map((animation) => animation.finished),
    ),
  );
}
