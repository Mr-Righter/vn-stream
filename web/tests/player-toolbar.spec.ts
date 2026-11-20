import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
test('gear menu, native actions and collapsible toolbar fit phone with no library exit', async ({
  page,
}) => {
  page.on('pageerror', (error) => console.log('player-error', error.message));
  const sent: any[] = [];
  await page.route('**/player-tools?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: `<html><body><button data-virtual-keyboard-button aria-label="Virtual Keyboard">·</button><input id="keyboard-input-assist"><div id="overlayInput"></div><script type="module" src="/game-controls.js"></script></body></html>`,
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
  await page.route('**/api/v2/auth/session', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { csrf_token: 'test-csrf' }, 200) }),
  );
  await page.routeWebSocket('**/api/v2/session/control', (ws) => {
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      sent.push(message);
      if (message.type === 'hello')
        ws.send(
          JSON.stringify({
            type: 'status',
            controller: true,
            epoch: 'epoch',
            ready: true,
            skip: false,
            supported: ['rollback', 'skip', 'save', 'load', 'menu', 'quit'],
            available: ['rollback', 'skip', 'save', 'load', 'menu', 'quit'],
          }),
        );
      if (message.type === 'action')
        ws.send(
          JSON.stringify({ type: 'action', request_id: message.request_id, state: 'accepted' }),
        );
    });
  });
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/player-tools?session=' + 'a'.repeat(32));
  await expect(page.getByRole('button', { name: '游戏设置', exact: true })).toBeVisible();
  const gearBox = await page.getByRole('button', { name: '游戏设置', exact: true }).boundingBox();
  expect(gearBox!.x).toBeGreaterThanOrEqual(12);
  expect(gearBox!.y).toBeGreaterThanOrEqual(12);
  await expect(page.getByRole('button', { name: '返回游戏库', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '存档', exact: true })).toBeEnabled();
  await expect(page.locator('[data-virtual-keyboard-button]')).toBeHidden();
  const toolbar = await page.locator('#game-shortcuts').elementHandle();
  const helloCount = sent.filter((message) => message.type === 'hello').length;
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '0ms');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect(page.locator('.vn-app')).toHaveCSS('--vn-motion-mid', '180ms');
  expect(await toolbar!.evaluate((element) => element.isConnected)).toBe(true);
  expect(sent.filter((message) => message.type === 'hello')).toHaveLength(helloCount);
  await page.getByRole('button', { name: '键盘', exact: true }).click();
  await expect(page.locator('#keyboard-input-assist')).toBeFocused();
  expect(
    await page
      .locator('#keyboard-input-assist')
      .evaluate((node) => node.closest('.allow-native-input')),
  ).toBeNull();
  await page.getByRole('button', { name: '快进', exact: true }).click();
  await expect.poll(() => sent.filter((m) => m.action === 'skip').length).toBe(1);
  await page.getByRole('button', { name: '存档', exact: true }).click();
  await expect.poll(() => sent.filter((m) => m.action === 'save').length).toBe(1);
  await page.getByRole('button', { name: '收起快捷栏', exact: true }).click();
  await expect(page.locator('#game-shortcuts')).toBeHidden();
  await page.getByRole('button', { name: '游戏设置', exact: true }).click();
  await page.locator('.shortcut-switch').getByRole('switch').click();
  await expect(page.locator('#game-shortcuts')).toBeVisible();
  await page.getByRole('button', { name: '退出游戏', exact: true }).click();
  await expect.poll(() => sent.filter((m) => m.action === 'quit').length).toBe(1);
  await page.getByRole('button', { name: '游戏设置', exact: true }).click();
  await expect(page.locator('.quit-game')).toBeDisabled();
  await page.locator('.quit-game').evaluate((node: HTMLButtonElement) => node.click());
  expect(sent.filter((message) => message.action === 'quit')).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('game deletion requires confirmation and removes complete self-authored project', async ({
  page,
  request,
}) => {
  const { mkdir, writeFile, stat } = await import('node:fs/promises');
  const { resolve } = await import('node:path');
  const title = 'Delete fixture ' + Date.now();
  const root = resolve('../var/e2e/inbox', title);
  await mkdir(root + '/game/saves', { recursive: true });
  await writeFile(root + '/game/script.rpy', 'label start:\n    return');
  await writeFile(root + '/game/saves/existing.save', 'fixture');
  const game = apiData(
    await (await request.post('/api/v2/projects', { data: { path: root } })).json(),
  );
  await page.goto('/#projects/' + game.project_id);
  await page.getByRole('button', { name: '删除游戏', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText(root);
  await expect(page.getByRole('dialog')).toContainText('不可恢复');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  expect((await stat(root + '/game/saves/existing.save')).isFile()).toBe(true);
  await page.getByRole('button', { name: '删除游戏', exact: true }).click();
  await page.getByRole('button', { name: '永久删除', exact: true }).click();
  await expect(page.locator('h1')).toHaveText('我的游戏');
  await expect
    .poll(async () =>
      apiData(await (await request.get('/api/v2/projects')).json()).some(
        (g: any) => g.id === game.project_id,
      ),
    )
    .toBe(false);
});
for (const width of [320, 390, 844])
  for (const language of ['zh', 'en'])
    for (const theme of ['dark', 'light']) {
      test(`toolbar layout ${width} ${language} ${theme}`, async ({ page }) => {
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
        await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
        await page.route('**/player-layout?*', (r) =>
          r.fulfill({
            contentType: 'text/html',
            body: '<html><body><script type="module" src="/game-controls.js"></script></body></html>',
          }),
        );
        await page.route('**/api/v2/session', (r) =>
          r.fulfill({
            json: mockEnvelope(
              r.request().url(),
              { state: 'running', session_id: 'a'.repeat(32) },
              200,
            ),
          }),
        );
        await page.routeWebSocket('**/api/v2/session/control', (ws) =>
          ws.onMessage(() =>
            ws.send(
              JSON.stringify({
                type: 'status',
                controller: true,
                epoch: 'layout',
                ready: true,
                skip: false,
                supported: ['menu', 'quit', 'skip', 'save', 'load', 'rollback'],
                available: ['menu', 'quit', 'skip', 'save', 'load', 'rollback'],
              }),
            ),
          ),
        );
        await page.goto('/player-layout?session=' + 'a'.repeat(32));
        const gear = page.getByRole('button', {
          name: language === 'en' ? 'Game settings' : '游戏设置',
          exact: true,
        });
        await expect(gear).toBeInViewport();
        const bar = await page.locator('#game-shortcuts').boundingBox();
        if (width === 390) expect(bar!.height).toBeLessThan(68);
        if (width === 844) expect(bar!.width).toBeLessThan(520);
        const buttons = page.locator('#game-shortcuts button');
        for (const button of await buttons.all()) {
          await expect(button).toBeInViewport();
          const rect = await button.boundingBox();
          expect(rect!.height).toBeGreaterThanOrEqual(44);
          expect(rect!.width).toBeGreaterThanOrEqual(44);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await gear.click();
        await expect(page.locator('.vn-player-menu')).not.toHaveCSS(
          'background-color',
          'rgba(0, 0, 0, 0)',
        );
        await expect(
          page.getByRole('button', {
            name: language === 'en' ? 'Quit game' : '退出游戏',
            exact: true,
          }),
        ).toBeInViewport();
      });
    }

test('supported shortcuts keep their positions through initialization and game context changes', async ({
  page,
}) => {
  const supported = ['rollback', 'skip', 'save', 'load', 'menu', 'quit'];
  const sent: any[] = [];
  let emit: (available: string[], ready: boolean) => void = () => {};
  await page.route('**/stable-toolbar?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<html><body><script type="module" src="/game-controls.js"></script></body></html>',
    }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: { data: { state: 'running', session_id: 'a'.repeat(32) } } }),
  );
  await page.route('**/api/v2/auth/session', (route) =>
    route.fulfill({ json: { data: { csrf_token: 'fixture' } } }),
  );
  await page.routeWebSocket('**/api/v2/session/control', (ws) => {
    emit = (available, ready) =>
      ws.send(
        JSON.stringify({
          type: 'status',
          controller: true,
          epoch: 'epoch',
          ready,
          skip: false,
          supported,
          available,
        }),
      );
    ws.onMessage((raw) => {
      const value = JSON.parse(String(raw));
      sent.push(value);
      if (value.type === 'hello') emit([], false);
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/stable-toolbar?session=' + 'a'.repeat(32));
  const bar = page.locator('#game-shortcuts');
  await expect(bar.getByRole('button')).toHaveCount(7);
  const before = await bar.locator('button').allTextContents();
  const rollback = bar.getByRole('button', { name: '上一句', exact: true });
  await expect(rollback).toBeDisabled();
  const originalNode = await rollback.elementHandle();
  emit(['menu', 'quit', 'load'], true);
  await expect(bar.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
  await expect(rollback).toBeDisabled();
  expect(await bar.locator('button').allTextContents()).toEqual(before);
  emit(supported, true);
  await expect(rollback).toBeEnabled();
  await rollback.click();
  await expect
    .poll(() => sent.filter((m) => m.type === 'action' && m.action === 'rollback').length)
    .toBe(1);
  emit(['menu', 'quit'], true);
  await expect(rollback).toBeDisabled();
  expect(await originalNode!.evaluate((node) => node.isConnected)).toBe(true);
  expect(await bar.locator('button').allTextContents()).toEqual(before);
});
