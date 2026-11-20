import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
// Catches equal-width hosts whose internal hit/label boxes remain content-sized.
for (const width of [320, 390, 844, 1440]) {
  for (const language of ['zh', 'en']) {
    for (const theme of ['dark', 'light']) {
      test(`source segments fill and center at ${width}, ${language}, ${theme}`, async ({
        page,
      }) => {
        await page.addInitScript(
          ({ language, theme }) => {
            localStorage.setItem(
              'vn-stream:preferences',
              JSON.stringify({
                ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
                language: language,
              }),
            );
            localStorage.setItem(
              'vn-stream:preferences',
              JSON.stringify({
                ...JSON.parse(localStorage.getItem('vn-stream:preferences') ?? '{}'),
                appearance: theme,
              }),
            );
          },
          { language, theme },
        );
        await page.route('**/api/v2/**', (route) => {
          const path = new URL(route.request().url()).pathname.split('/api/v2/')[1];
          return route.fulfill({
            json: mockEnvelope(
              route.request().url(),
              path === 'projects' || path === 'runtimes'
                ? []
                : path === 'engines'
                  ? engineFixtures
                  : path === 'imports/sources'
                    ? { roots: [], entries: [] }
                    : path === 'session'
                      ? { state: 'stopped' }
                      : null,
              200,
            ),
          });
        });
        await page.setViewportSize({ width, height: 900 });
        await page.goto('/#add');
        const tabs = page.locator('#source-tabs .ant-segmented-item');
        await expect(tabs).toHaveCount(3);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        const geometry = await tabs.evaluateAll((nodes) =>
          nodes.map((node) => {
            const host = node.getBoundingClientRect();
            const inner = node.querySelector<HTMLElement>('.ant-segmented-item-label')!;
            const box = inner.getBoundingClientRect();
            const text = document.createRange();
            text.selectNodeContents(inner);
            const label = text.getBoundingClientRect();
            return {
              width: host.width,
              height: host.height,
              innerWidth: box.width,
              centerError: Math.abs(label.left + label.width / 2 - (host.left + host.width / 2)),
              textHeight: label.height,
              innerHeight: box.height,
            };
          }),
        );
        for (const item of geometry) {
          expect(item.width).toBeCloseTo(geometry[0].width, 0);
          expect(item.height).toBeCloseTo(geometry[0].height, 0);
          expect(item.height).toBeGreaterThanOrEqual(44);
          expect(item.innerWidth).toBeCloseTo(item.width, 0);
          expect(item.centerError).toBeLessThanOrEqual(1);
          expect(item.textHeight).toBeLessThanOrEqual(item.innerHeight);
        }
        const group = await page.locator('#source-tabs').locator('..').boundingBox();
        const card = await page.locator('.import-card').boundingBox();
        expect(group!.x).toBeCloseTo(card!.x, 0);
        expect(group!.width).toBeCloseTo(card!.width, 0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        const first = tabs.nth(0);
        const last = tabs.nth(2);
        await first.getByRole('radio').focus();
        await page.keyboard.press('ArrowRight');
        await expect(tabs.nth(1).getByRole('radio')).toBeChecked();
        await expect(page.locator('#folder-source')).toBeVisible();
        await last.click();
        await expect(last.getByRole('radio')).toBeChecked();
        await expect(last).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
        const colors = await tabs.evaluateAll((nodes) =>
          nodes.map((node) => getComputedStyle(node).backgroundColor),
        );
        expect(colors[2]).not.toBe('rgba(0, 0, 0, 0)');
        expect(colors[2]).not.toBe(colors[0]);
      });
    }
  }
}
