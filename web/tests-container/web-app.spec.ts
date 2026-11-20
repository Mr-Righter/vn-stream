import { test, expect } from './fixtures.js';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { probe } from './support.js';

test('one public manifest includes login, library and authenticated player', async ({
  page,
  request,
  playwright,
  baseURL,
}) => {
  expect(baseURL).toMatch(/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/);
  const anonymous = await playwright.request.newContext({ baseURL });
  try {
    const response = await anonymous.get('/manifest.webmanifest');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('application/manifest+json');
    const manifest = await response
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(manifest).toMatchObject({
      id: '/',
      scope: '/',
      start_url: '/library/',
      display: 'standalone',
    });
    expect((await anonymous.get('/manifest.json')).status()).toBe(401);
    expect(manifest.icons.map((icon: { sizes: string }) => icon.sizes)).toEqual([
      '192x192',
      '512x512',
    ]);
    const tabIcon = await anonymous.get('/favicon.ico', { maxRedirects: 0 });
    expect(tabIcon.status()).toBe(200);
    expect(tabIcon.headers()['content-type']).toBe('image/x-icon');
    expect((await tabIcon.body()).subarray(0, 8).toString('hex')).toBe('0000010001002020');
    expect((await anonymous.head('/favicon.ico')).status()).toBe(200);
    expect((await anonymous.post('/favicon.ico', { maxRedirects: 0 })).status()).toBe(403);
    const smallIcon = { src: '/app-icons/icon-32.png', sizes: '32x32' };
    for (const icon of [smallIcon, ...manifest.icons]) {
      const image = await anonymous.get(icon.src, { maxRedirects: 0 });
      expect(image.status()).toBe(200);
      expect(image.headers()['content-type']).toBe('image/png');
      const body = await image.body();
      expect(body.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
      expect(`${body.readUInt32BE(16)}x${body.readUInt32BE(20)}`).toBe(icon.sizes);
      expect((await anonymous.head(icon.src, { maxRedirects: 0 })).status()).toBe(200);
    }
    expect((await anonymous.post(smallIcon.src, { maxRedirects: 0 })).status()).toBe(403);
    for (const path of ['/api/v2/projects', '/api/v2/session', '/api/health'])
      expect((await anonymous.get(path)).status()).toBe(401);
    expect((await anonymous.get('/app-icons/private.txt', { maxRedirects: 0 })).status()).toBe(401);
    for (const path of ['/library/login', '/library/', '/', '/index.html']) {
      const client = path === '/library/login' ? anonymous : request;
      const html = await (await client.get(path)).text();
      expect(html).toMatch(/rel="manifest" href="\/manifest\.webmanifest"/);
      expect(html).toContain('rel="icon" type="image/x-icon" sizes="32x32" href="/favicon.ico"');
      expect(html).toContain(
        'rel="apple-touch-icon" sizes="192x192" href="/app-icons/icon-192.png"',
      );
      expect(html).toContain('name="apple-mobile-web-app-capable" content="yes"');
      expect(html).toContain('viewport-fit=cover');
      expect(new URL(path, baseURL).pathname.startsWith(manifest.scope)).toBe(true);
    }
    await page.goto('/library/');
    await expect(page.locator('link[rel=manifest]')).toHaveCount(1);
    expect((await request.get('/api/v2/session')).ok()).toBe(true);
  } finally {
    await anonymous.dispose();
  }
});

test('invisible original keyboard receiver forwards IME, backspace and enter through real media', async ({
  baseURL,
  page,
  request,
}) => {
  expect(baseURL).toMatch(/^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/);
  const name = 'Keyboard-' + randomUUID().slice(0, 8);
  const root = resolve(dirname(probe), name);
  await mkdir(root + '/game', { recursive: true });
  await writeFile(
    root + '/game/script.rpy',
    `# Self-authored IME fixture; no third-party content.
define config.screen_width = 960
define config.screen_height = 540
init python:
    import json, os
    gui.init(960,540)
    def record(kind,value=None):
        with open(os.path.join(config.basedir,"keyboard.jsonl"),"a") as f:
            f.write(json.dumps({"kind":kind,"value":value}) + "\\n")
screen main_menu():
    tag menu
    add Solid("#23465c")
    text "Keyboard test" align (.5,.5)
    key "K_RETURN" action Start()
label start:
    $ record("input-ready")
    $ result = renpy.input("Type a name")
    $ record("result",result)
    while True:
        $ renpy.pause(1.0,hard=True)
`,
  );
  const game = await (
    await request.post('/api/v2/projects', { data: { path: '/projects/' + name } })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const started = await request.post(`/api/v2/projects/${game.project_id}/start`, {
    data: { runtime_id: 'renpy:8.3.2' },
  });
  expect(started.status()).toBe(200);
  const session = await started
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const events = async () => {
    try {
      return (await readFile(root + '/keyboard.jsonl', 'utf8'))
        .trim()
        .split('\n')
        .filter(Boolean)
        .map((s) => JSON.parse(s));
    } catch {
      return [];
    }
  };
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/?session=' + session.session_id);
    await page.waitForFunction(
      () => ((document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth ?? 0) > 0,
    );
    await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
    const video = await page.locator('#videoStream').boundingBox();
    await page.mouse.click(video!.x + video!.width * 0.7, video!.y + video!.height * 0.3);
    await page.keyboard.press('Enter');
    await expect
      .poll(async () => (await events()).some((e) => e.kind === 'input-ready'))
      .toBe(true);
    const input = page.locator('#keyboard-input-assist');
    await page.getByRole('button', { name: '键盘', exact: true }).click();
    await expect(input).toBeFocused();
    await page.getByRole('button', { name: '收起快捷栏', exact: true }).click();
    await expect(input).not.toBeFocused();
    await page.getByRole('button', { name: '游戏设置', exact: true }).click();
    await page.locator('.shortcut-switch').getByRole('switch').click();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '键盘', exact: true }).click();
    await expect(input).toBeFocused();
    await expect(input).toHaveCSS('opacity', '0');
    const box = await input.boundingBox();
    expect(box!.width).toBeLessThanOrEqual(1);
    expect(box!.height).toBeLessThanOrEqual(1);
    expect(await input.getAttribute('placeholder')).toBeNull();
    expect(await input.evaluate((el) => !!el.closest('.allow-native-input'))).toBe(false);
    await page.setViewportSize({ width: 844, height: 390 });
    await expect(input).toBeFocused();
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.imeSetComposition', { text: '测试', selectionStart: 2, selectionEnd: 2 });
    await cdp.send('Input.insertText', { text: '测试' });
    await page.keyboard.type('ABC');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('Backspace');
    await page.keyboard.press('Enter');
    await expect
      .poll(async () => (await events()).find((e) => e.kind === 'result')?.value)
      .toBe('测试A');
    const after = await (
      await request.get('/api/v2/session')
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(after.session_id).toBe(session.session_id);
    expect(after.display.capture).toEqual(session.display.capture);
    const media = await page.locator('#videoStream').boundingBox();
    await page.mouse.click(media!.x + media!.width * 0.7, media!.y + media!.height * 0.3);
    await expect(input).not.toBeFocused();
    await page.screenshot({
      path: '../var/antd-work/invisible-keyboard-landscape.png',
    });
    await cdp.detach();
  } finally {
    await request.post('/api/v2/session/stop');
  }
});
