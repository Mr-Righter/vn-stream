import { mockEnvelope, renpyProjectFixture } from './api-fixtures.js';
import { test, expect, setAppearance } from './fixtures.js';
const names = ['AbyssalLust', 'Agent17', 'Braveheart Academy', 'ECHOES', '游戏项目'];
const games = names.map((name, i) =>
  renpyProjectFixture({
    project_id: String(i + 1).padStart(32, '0'),
    name,
    release_version: '0.9.0',
    size_bytes: 1024,
    last_played_at: new Date((100 + i) * 1000).toISOString(),
  }),
);
async function fixture(page: import('@playwright/test').Page) {
  await page.route('**/api/v2/projects', (r) =>
    r.fulfill({ json: mockEnvelope(r.request().url(), games, 200) }),
  );
  await page.route('**/api/v2/session', (r) =>
    r.fulfill({ json: mockEnvelope(r.request().url(), { state: 'stopped' }, 200) }),
  );
  await page.goto('/');
  await expect(page.locator('.game-row')).toHaveCount(5);
}
for (const width of [390, 960]) {
  test(`group spacing, separators and row containment at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await fixture(page);
    await expect(page.locator('.game-group')).toHaveCount(4);
    const layout = await page.evaluate(() => {
      const groups = [...document.querySelectorAll('.game-group')];
      const row = document.querySelector('.game-row')!;
      const rect = row.getBoundingClientRect();
      const text = row.querySelector('.row-text')!.getBoundingClientRect();
      return {
        gaps: groups
          .slice(1)
          .map((g, i) => g.getBoundingClientRect().top - groups[i].getBoundingClientRect().bottom),
        headingGap:
          row.getBoundingClientRect().top -
          groups[0].querySelector('.letter')!.getBoundingClientRect().bottom,
        contains: [...row.children].every((c) => {
          const r = c.getBoundingClientRect();
          return r.top >= rect.top && r.bottom <= rect.bottom;
        }),
        line: getComputedStyle(row, '::after').height,
        lineStart: rect.left + parseFloat(getComputedStyle(row, '::after').left),
        textStart: text.left,
        lastLine: getComputedStyle(groups[0].querySelector('.game-row:last-child')!, '::after')
          .content,
        surface: getComputedStyle(document.querySelector('#games')!).backgroundColor,
        textBorder: getComputedStyle(row.querySelector('.row-text')!).borderTopWidth,
        textPadding: getComputedStyle(row.querySelector('.row-text')!).paddingTop,
      };
    });
    expect(layout.gaps).toEqual([40, 40, 40]);
    expect(layout.headingGap).toBe(12);
    expect(layout.contains).toBe(true);
    expect(layout.line).toBe('1px');
    expect(layout.lineStart).toBeCloseTo(layout.textStart, 1);
    expect(layout.lastLine).toBe('none');
    expect(layout.surface).toBe('rgba(0, 0, 0, 0)');
    expect(layout.textBorder).toBe('0px');
    expect(layout.textPadding).toBe('0px');
    const before = await page.locator('.game-row').first().boundingBox();
    await page.locator('.game-row').first().hover();
    expect(await page.locator('.game-row').first().boundingBox()).toEqual(before);
    await page.locator('#recent').click();
    await expect(page.locator('.letter')).toHaveCount(0);
    await expect(page.locator('.game-row').first()).toHaveAttribute('aria-label', '游戏项目');
  });
}
test('long names wrap inside the complete row in both themes', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  games[0].name = 'A very long game name that needs multiple lines to fit on a phone';
  try {
    await fixture(page);
    for (const theme of ['dark', 'light']) {
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
      }, theme);
      expect(
        await page
          .locator('.game-row')
          .first()
          .evaluate((e) => {
            const r = e.getBoundingClientRect(),
              n = e.querySelector('.game-name')!.getBoundingClientRect();
            return (
              n.left >= r.left &&
              n.right <= r.right &&
              n.top >= r.top &&
              n.bottom <= r.bottom &&
              document.documentElement.scrollWidth <= innerWidth
            );
          }),
      ).toBe(true);
    }
  } finally {
    games[0].name = names[0];
  }
});
test('selected segment is lighter than its track in both themes', async ({ page }) => {
  await fixture(page);
  for (const theme of ['dark', 'light']) {
    await setAppearance(page, theme);
    await expect(page.locator('#add-game')).not.toBeVisible();
    const colors = await page.locator('.segments').evaluate((track) => {
      // Let the browser resolve rgb(), color-mix(), and color(srgb ...) alike.
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 1;
      const context = canvas.getContext('2d', { willReadFrequently: true })!;
      const luminance = (color: string) => {
        context.fillStyle = color;
        context.fillRect(0, 0, 1, 1);
        return Array.from(context.getImageData(0, 0, 1, 1).data.slice(0, 3))
          .map((value) => value / 255)
          .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
          .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
      };
      return {
        track: luminance(getComputedStyle(track).backgroundColor),
        selected: luminance(
          getComputedStyle(track.querySelector('.ant-segmented-item-selected')!).backgroundColor,
        ),
      };
    });
    expect(colors.selected).toBeGreaterThan(colors.track);
  }
});
