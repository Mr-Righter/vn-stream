import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
test('library opens details with static metadata and nearest engine', async ({ page, request }) => {
  const title = `Detail Story ${Date.now()}`;
  const root = resolve('../var/e2e/inbox', `Detail-${Date.now()}`);
  await mkdir(`${root}/game`, { recursive: true });
  await mkdir(`${root}/renpy`);
  await writeFile(
    `${root}/game/options.rpy`,
    `define config.name = "${title}"\ndefine config.version = "0.9"`,
  );
  await writeFile(`${root}/game/script.rpyc`, 'fixture');
  await writeFile(`${root}/renpy/__init__.py`, 'version_tuple = (8,3,6,0)');
  const sdk = resolve('../var/e2e/data/runtimes/renpy/8.3.7');
  await mkdir(`${sdk}/renpy`, { recursive: true });
  await writeFile(`${sdk}/renpy/__init__.py`, 'version_tuple = (8,3,7,0)');
  await writeFile(`${sdk}/renpy.sh`, 'sleep 30');
  const game = apiData(
    await (await request.post('/api/v2/projects', { data: { path: root } })).json(),
  );
  await page.goto('/');
  await page.getByRole('button', { name: title, exact: true }).click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await expect(
    page.locator('main > #details').getByText('0.9', { exact: true }).first(),
  ).toBeVisible();
  await expect(page.locator('#details .vn-select-value')).toContainText('推荐');
  await expect(page.locator('#details .vn-select-value')).toHaveText('8.3.7（推荐）');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => scrollTo(0, 0));
  await page.waitForTimeout(150);
  await expect(page.getByRole('button', { name: '开始', exact: true })).toBeInViewport();
  await page.screenshot({ path: '../var/antd-work/mobile-dark-details.png' });
  await page.getByRole('combobox', { name: '引擎版本' }).click();
  await expect(page.getByRole('option', { name: '8.3.7（推荐）', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '返回游戏库', exact: true }).click();
  await expect(page.locator('h1')).toHaveText('我的游戏');
  expect(game.project_id).toBeTruthy();
});
