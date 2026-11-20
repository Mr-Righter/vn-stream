import { mockEnvelope, renpyProjectFixture } from './api-fixtures.js';
import { test, expect, setAppearance, type Page } from './fixtures.js';
const games = [
  {
    project_id: 'legacy',
    name: 'Alpha Legacy',
    last_played_at: new Date(100 * 1000).toISOString(),
  },
  {
    project_id: 'ons',
    name: 'Alpha Story',
    engine_id: 'ons',
    last_played_at: new Date(300 * 1000).toISOString(),
  },
  { project_id: 'renpy', name: 'Alpha Story', engine_id: 'renpy', last_played_at: null },
  {
    project_id: 'bravo',
    name: 'Bravo',
    engine_id: 'renpy',
    last_played_at: new Date(200 * 1000).toISOString(),
  },
  {
    project_id: 'krkr',
    name: '中文故事',
    engine_id: 'krkr',
    last_played_at: new Date(400 * 1000).toISOString(),
  },
].map((game) => renpyProjectFixture({ release_version: '1.0', size_bytes: 1024, ...game }));
async function fixture(page: Page, running = false) {
  const requests: string[] = [];
  let collection = [...games];
  await page.route('**/api/v2/projects', (route) => {
    requests.push(route.request().url());
    return route.fulfill({ json: mockEnvelope(route.request().url(), collection, 200) });
  });
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        running ? { state: 'running', project_id: 'bravo' } : { state: 'stopped' },
        200,
      ),
    }),
  );
  await page.goto('/');
  await expect(page.locator('.game-row')).toHaveCount(5);
  return {
    requests,
    update: (next: typeof games) => {
      collection = next;
    },
  };
}
async function engine(page: Page, name: string) {
  await page.getByRole('combobox', { name: '引擎筛选', exact: true }).click();
  await page.getByRole('option', { name, exact: true }).click();
}
test('name and engine filters combine locally with recent sorting and legacy records', async ({
  page,
}) => {
  const { requests } = await fixture(page);
  const search = page.getByRole('textbox', { name: '搜索游戏名称', exact: true });
  await search.fill('  aLPha  ');
  await expect(page.locator('.game-row')).toHaveCount(3);
  await expect(page.locator('.letter')).toHaveText(['A']);
  await engine(page, "Ren'Py");
  await expect(page.locator('.game-name')).toHaveText(['Alpha Legacy', 'Alpha Story']);
  await page.locator('#recent').click();
  await expect(page.locator('.game-name')).toHaveText(['Alpha Legacy']);
  await engine(page, 'ONScripter');
  await expect(page.locator('.game-name')).toHaveText(['Alpha Story']);
  await engine(page, '全部引擎');
  await search.fill('');
  await expect(page.locator('.game-name')).toHaveText([
    '中文故事',
    'Alpha Story',
    'Bravo',
    'Alpha Legacy',
  ]);
  await search.fill('故事');
  await engine(page, 'Kirikiri');
  await expect(page.locator('.game-name')).toHaveText(['中文故事']);
  expect(requests.every((url) => new URL(url).search === '')).toBe(true);
  expect(requests.length).toBeLessThanOrEqual(2);
});
test('no match can be cleared and cannot hide the active game resume button', async ({ page }) => {
  await fixture(page, true);
  await page.getByRole('textbox', { name: '搜索游戏名称' }).fill('missing game');
  await expect(page.locator('.game-row')).toHaveCount(0);
  await expect(
    page.getByText('没有匹配的游戏，请尝试其他名称或引擎。', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: '继续 Bravo', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '清除筛选', exact: true }).click();
  await expect(page.locator('.game-row')).toHaveCount(5);
});
test('details, refresh and preference changes retain tab filters but a new tab starts clean', async ({
  page,
  context,
}) => {
  await fixture(page);
  await page.getByRole('textbox', { name: '搜索游戏名称' }).fill('Alpha');
  await engine(page, 'ONScripter');
  await page.locator('#recent').click();
  await page.getByRole('button', { name: 'Alpha Story', exact: true }).click();
  await expect(page.locator('#details')).toBeVisible();
  await page.getByRole('button', { name: '返回游戏库', exact: true }).click();
  await expect(page.locator('.game-row')).toHaveCount(1);
  await page.reload();
  await expect(page.getByRole('textbox', { name: '搜索游戏名称' })).toHaveValue('Alpha');
  await expect(page.locator('.game-row')).toHaveCount(1);
  await expect(page.getByRole('radio', { name: '最近玩过', exact: true })).toBeChecked();
  await page.locator('#language-toggle').click();
  await page.getByRole('menuitem', { name: 'English', exact: true }).click();
  await setAppearance(page, 'light');
  await expect(page.getByRole('textbox', { name: 'Search games by name' })).toHaveValue('Alpha');
  await expect(page.locator('.game-row')).toHaveCount(1);
  const other = await context.newPage();
  try {
    await other.route('**/api/v2/projects', (route) =>
      route.fulfill({ json: mockEnvelope(route.request().url(), games, 200) }),
    );
    await other.goto('/');
    await expect(other.locator('.game-row')).toHaveCount(5);
    await expect(other.getByRole('textbox', { name: 'Search games by name' })).toHaveValue('');
  } finally {
    await other.close();
  }
});
test('polling reapplies filters to newly added and removed records', async ({ page }) => {
  const { update } = await fixture(page);
  await page.getByRole('textbox', { name: '搜索游戏名称' }).fill('New game');
  await engine(page, 'Kirikiri');
  const next = { ...games[4], project_id: 'new', name: 'New game' };
  update([...games, next]);
  await expect(page.locator('.game-name')).toHaveText(['New game'], { timeout: 8000 });
  update(games);
  await expect(page.locator('.game-row')).toHaveCount(0, { timeout: 8000 });
});
test('blocked session storage does not break filtering', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'sessionStorage', {
      get() {
        throw new Error('blocked');
      },
    });
  });
  await fixture(page);
  await page.getByRole('textbox', { name: '搜索游戏名称' }).fill('Bravo');
  await expect(page.locator('.game-name')).toHaveText(['Bravo']);
});
test('invalid stored filters fall back and the localized clear button is keyboard accessible', async ({
  page,
}) => {
  await page.addInitScript(() => sessionStorage.setItem('vn-stream-library-filters', '{invalid'));
  await fixture(page);
  await page.getByRole('textbox', { name: '搜索游戏名称' }).fill('Bravo');
  const clear = page.getByRole('button', { name: '清除搜索', exact: true });
  await clear.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.game-row')).toHaveCount(5);
});
for (const width of [320, 390, 960]) {
  test(`filter controls fit and remain keyboard accessible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await fixture(page);
    for (const appearance of ['light', 'dark']) {
      await setAppearance(page, appearance);
      const search = page.getByRole('textbox', { name: '搜索游戏名称' });
      const select = page.getByRole('combobox', { name: '引擎筛选', exact: true });
      await select.click();
      await expect(select).toHaveAttribute('readonly');
      await page.keyboard.press('Escape');
      await search.focus();
      await page.keyboard.type('Bravo');
      await expect(page.locator('.game-name')).toHaveText(['Bravo']);
      await search.fill('');
      const boxes = await page.locator('.vn-library-filters').evaluate((root) => {
        const input = root.querySelector('.ant-input-affix-wrapper')!.getBoundingClientRect();
        const select = root.querySelector('.ant-select')!.getBoundingClientRect();
        return {
          input: { y: input.y, height: input.height },
          select: { y: select.y, height: select.height },
          overflow: document.documentElement.scrollWidth > innerWidth,
        };
      });
      expect(boxes.overflow).toBe(false);
      expect(boxes.input.height).toBeGreaterThanOrEqual(44);
      expect(boxes.select.height).toBeGreaterThanOrEqual(44);
      expect(width < 600 ? boxes.select.y > boxes.input.y : boxes.select.y === boxes.input.y).toBe(
        true,
      );
    }
  });
}
