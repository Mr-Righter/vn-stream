import { test, expect } from './fixtures.js';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

const evidence = process.env.VN_STREAM_EVIDENCE_DIR ?? '../var/container-verify';
const versions = ['7.5.3', '7.8.2', '8.0.3', '8.1.3', '8.2.3', '8.3.2', '8.3.7', '8.4.1', '8.5.3'];
async function fixture(version: string, width = 960, height = 540) {
  const name = `Lifecycle-${version}-${randomUUID().slice(0, 8)}`;
  const root = resolve('../var/container-verify/inbox', name);
  await mkdir(root + '/game', { recursive: true });
  await writeFile(
    root + '/game/script.rpy',
    `# Self-authored lifecycle and IME fixture; no third-party assets.
define config.name = "${name}"
define config.screen_width = ${width}
define config.screen_height = ${height}
init python:
    import json, os
    gui.init(${width}, ${height})
    def record(kind, value=None):
        with open(os.path.join(config.basedir, "lifecycle.jsonl"), "a") as output:
            output.write(json.dumps({"kind":kind, "value":value}) + "\\n")
    config.quit_action = [Function(record, "quit-requested"), Confirm("Exit this fixture?", Quit(confirm=False), Function(record,"quit-cancelled"))]
    config.overlay_screens.append("fixture_keys")
    _preferences.skip_unseen = True
screen fixture_keys():
    key "K_i" action Jump("ask_name")
screen main_menu():
    tag menu
    add Solid("#22364d")
    text "Lifecycle fixture" xpos 100 ypos 100
    key "K_RETURN" action Start()
    on "show" action Function(record,"main")
screen save():
    tag menu
    add Solid("#253d34")
    text "SAVE" xpos 100 ypos 100
    key "K_ESCAPE" action Return()
    on "show" action Function(record,"save")
    on "hide" action Function(record,"save-closed")
screen load():
    tag menu
    add Solid("#453240")
    text "LOAD" xpos 100 ypos 100
    key "K_ESCAPE" action Return()
    on "show" action Function(record,"load")
    on "hide" action Function(record,"load-closed")
screen confirm(message,yes_action,no_action):
    modal True
    zorder 200
    add Solid("#182028")
    text message xpos 100 ypos 100
    key "K_n" action no_action
    key "K_y" action yes_action
label start:
    $ record("ready")
    while True:
        "A clear line of dialogue."
        "Another line for rollback."
label ask_name:
    $ record("input-ready")
    $ value = renpy.input("Type a name")
    $ record("input",value)
    jump start
`,
  );
  return { root, path: '/projects/' + name };
}
async function rows(root: string) {
  try {
    return (await readFile(root + '/lifecycle.jsonl', 'utf8'))
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((l) => JSON.parse(l));
  } catch {
    return [];
  }
}
async function ready(page: any) {
  await page.waitForFunction(
    () => ((document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth ?? 0) > 0,
  );
}
async function focus(page: any) {
  const box = await page.locator('#videoStream').boundingBox();
  await page.mouse.click(box.x + box.width * 0.7, box.y + box.height * 0.35);
}
for (const version of versions) {
  test(`native lifecycle ${version}: menus, keyboard, quit confirmation and cancel`, async ({
    page,
    request,
  }) => {
    page.on('websocket', (socket) => {
      if (new URL(socket.url()).pathname === '/api/websockets')
        socket.on('framesent', ({ payload }) => {
          if (typeof payload === 'string' && /^(kd|ku),/.test(payload))
            console.log('fixture-key', payload);
        });
    });
    const f = await fixture(version);
    const g = await (
      await request.post('/api/v2/projects', { data: { path: f.path } })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    const start = await request.post(`/api/v2/projects/${g.project_id}/start`, {
      data: { runtime_id: `renpy:${version}` },
    });
    expect(start.status()).toBe(200);
    const session = await start
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    try {
      await page.goto('/?session=' + session.session_id);
      await ready(page);
      await expect.poll(async () => (await rows(f.root)).some((r) => r.kind === 'main')).toBe(true);
      await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
      await focus(page);
      console.log(
        'fixture-focus',
        await page.evaluate(() => ({
          active: document.activeElement?.id,
          input: !!(window as any).webrtcInput,
          transport: (window as any).selkiesTransport?.readyState,
        })),
      );
      await page.screenshot({ path: `${evidence}/native-before.png` });
      await page.keyboard.press('Enter');
      await expect
        .poll(async () => (await rows(f.root)).some((r) => r.kind === 'ready'))
        .toBe(true);
      await expect(page.getByRole('button', { name: '存档', exact: true })).toBeEnabled();
      await page.getByRole('button', { name: '存档', exact: true }).click();
      await expect.poll(async () => (await rows(f.root)).some((r) => r.kind === 'save')).toBe(true);
      await focus(page);
      await page.keyboard.press('Escape');
      await expect
        .poll(async () => (await rows(f.root)).some((r) => r.kind === 'save-closed'))
        .toBe(true);
      await page.getByRole('button', { name: '读档', exact: true }).click();
      await expect.poll(async () => (await rows(f.root)).some((r) => r.kind === 'load')).toBe(true);
      await focus(page);
      await page.keyboard.press('Escape');
      await expect
        .poll(async () => (await rows(f.root)).some((r) => r.kind === 'load-closed'))
        .toBe(true);
      await page.getByRole('button', { name: '快进', exact: true }).click();
      await expect(page.getByRole('button', { name: '快进', exact: true })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
      await page.getByRole('button', { name: '快进', exact: true }).click();
      await expect(page.getByRole('button', { name: '快进', exact: true })).toHaveAttribute(
        'aria-pressed',
        'false',
      );
      await focus(page);
      await page.keyboard.press('i');
      await expect
        .poll(async () => (await rows(f.root)).some((r) => r.kind === 'input-ready'))
        .toBe(true);
      await page.getByRole('button', { name: '键盘', exact: true }).click();
      const input = page.locator('#keyboard-input-assist');
      await expect(input).toBeFocused();
      await page.keyboard.insertText('测试ABC');
      await page.keyboard.press('Enter');
      await expect
        .poll(async () => (await rows(f.root)).find((r) => r.kind === 'input')?.value)
        .toBe('测试ABC');
      await page.getByRole('button', { name: '游戏设置', exact: true }).click();
      await page.getByRole('button', { name: '退出游戏', exact: true }).click();
      await expect
        .poll(async () => (await rows(f.root)).filter((r) => r.kind === 'quit-requested').length)
        .toBe(1);
      await focus(page);
      await page.keyboard.press('n');
      await expect
        .poll(async () => (await rows(f.root)).some((r) => r.kind === 'quit-cancelled'))
        .toBe(true);
      expect(
        (
          await (
            await request.get('/api/v2/session')
          )
            .json()
            .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
        ).session_id,
      ).toBe(session.session_id);
      await page.getByRole('button', { name: '游戏设置', exact: true }).click();
      await page.getByRole('button', { name: '退出游戏', exact: true }).click();
      await expect
        .poll(async () => (await rows(f.root)).filter((r) => r.kind === 'quit-requested').length)
        .toBe(2);
      await focus(page);
      await page.keyboard.press('y');
      await expect(page).toHaveURL(/\/library\/$/, { timeout: 10000 });
      expect(
        (
          await (
            await request.get('/api/v2/session')
          )
            .json()
            .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
        ).state,
      ).toBe('stopped');
    } finally {
      await request.post('/api/v2/session/stop');
    }
  });
}

test('closing a real player expires in60s, reconnect and old socket do not kill replacement', async ({
  page,
  request,
  browser,
  baseURL,
}) => {
  test.setTimeout(110000);
  const f = await fixture('8.3.2');
  const g = await (
    await request.post('/api/v2/projects', { data: { path: f.path } })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const session = await (
    await request.post(`/api/v2/projects/${g.project_id}/start`, {
      data: { runtime_id: 'renpy:8.3.2' },
    })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  try {
    await page.goto('/?session=' + session.session_id);
    await ready(page);
    await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
    await page.reload();
    await ready(page);
    await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
    expect(
      (
        await (
          await request.get('/api/v2/session')
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).session_id,
    ).toBe(session.session_id);
    const second = await browser.newContext({
      baseURL,
      storageState: await page.context().storageState(),
    });
    const replacement = await second.newPage();
    await replacement.goto('/?session=' + session.session_id);
    await ready(replacement);
    await expect(replacement.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
    await page.close();
    await replacement.waitForTimeout(1500);
    expect(
      (
        await (
          await request.get('/api/v2/session')
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).state,
    ).toBe('running');
    const closedAt = Date.now();
    await second.close();
    await expect
      .poll(
        async () =>
          (
            await (
              await request.get('/api/v2/session')
            )
              .json()
              .then((body) =>
                body && typeof body === 'object' && 'data' in body ? body.data : body,
              )
          ).state,
        {
          timeout: 75000,
          intervals: [1000],
        },
      )
      .toBe('stopped');
    const ended = await (
      await request.get('/api/v2/session')
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(ended.reason).toBe('player_disconnected');
    expect(Date.now() - closedAt).toBeGreaterThan(45000);
    expect(Date.now() - closedAt).toBeLessThan(75000);
  } finally {
    await request.post('/api/v2/session/stop');
  }
});

for (const [width, height] of [
  [1920, 1080],
  [3840, 2160],
]) {
  test(`capture load ${width}x${height}: original geometry and NAS resource snapshot`, async ({
    page,
    request,
  }) => {
    const { execFile } = await import('node:child_process');
    const { promisify } = await import('node:util');
    const f = await fixture('8.3.2', width, height);
    const game = await (
      await request.post('/api/v2/projects', { data: { path: f.path } })
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
    try {
      await page.goto('/?session=' + session.session_id);
      await ready(page);
      await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
      await expect
        .poll(() =>
          page
            .locator('#videoStream')
            .evaluate((v: HTMLVideoElement) => [v.videoWidth, v.videoHeight]),
        )
        .toEqual([width, height]);
      await page.waitForTimeout(5000);
      const stats = JSON.parse(
        (
          await promisify(execFile)('sg', [
            'docker',
            '-c',
            'docker stats --no-stream --format "{{json .}}" vn-stream:verify-vn-stream:1',
          ])
        ).stdout,
      );
      const state = await (
        await request.get('/api/v2/session')
      )
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      expect(state.display.capture).toEqual([width, height]);
      const result = {
        width,
        height,
        cpu: stats.CPUPerc,
        memory: stats.MemUsage,
        network: stats.NetIO,
        capture: state.display.capture,
      };
      await writeFile(`${evidence}/load-${width}.json`, JSON.stringify(result, null, 2));
      await page.screenshot({ path: `${evidence}/player-${width}.png` });
    } finally {
      await request.post('/api/v2/session/stop');
    }
  });
}

test('duplicate native quit while confirmation is open is rejected without force exit', async ({
  page,
  request,
}) => {
  let duplicated = false,
    rejected = false;
  await page.routeWebSocket('**/api/v2/session/control', (route) => {
    const server = route.connectToServer();
    route.onMessage((raw) => {
      server.send(raw);
      const message = JSON.parse(String(raw));
      if (message.type === 'action' && message.action === 'quit' && !duplicated) {
        duplicated = true;
        server.send(JSON.stringify({ ...message, request_id: 'e'.repeat(32) }));
      }
    });
    server.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === 'action' && message.reason === 'quit_pending') rejected = true;
      route.send(raw);
    });
  });
  const f = await fixture('8.3.2');
  const game = await (
    await request.post('/api/v2/projects', { data: { path: f.path } })
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
  try {
    await page.goto('/?session=' + session.session_id);
    await ready(page);
    await page.getByRole('button', { name: '游戏设置', exact: true }).click();
    await expect(page.getByRole('button', { name: '退出游戏', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '退出游戏', exact: true }).click();
    await expect.poll(() => rejected).toBe(true);
    expect((await rows(f.root)).filter((r) => r.kind === 'quit-requested').length).toBe(1);
    expect(
      (
        await (
          await request.get('/api/v2/session')
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).state,
    ).toBe('running');
    await focus(page);
    await page.keyboard.press('n');
    await expect
      .poll(async () => (await rows(f.root)).some((r) => r.kind === 'quit-cancelled'))
      .toBe(true);
  } finally {
    await request.post('/api/v2/session/stop');
  }
});
