import { test, expect } from './fixtures.js';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';

test('media client avoids a stale stable script URL and binds after reload at phone width', async ({
  page,
  request,
}) => {
  const name = 'MobileMedia-' + randomUUID().slice(0, 8);
  const root = resolve('../var/container-verify/inbox', name);
  await mkdir(root + '/game', { recursive: true });
  await writeFile(
    root + '/game/script.rpy',
    `# Self-authored phone transport fixture, no external assets.
define config.screen_width = 1280
define config.screen_height = 720
init python:
    gui.init(1280,720)
screen main_menu():
    tag menu
    add Solid("#23465c")
    text "Phone media test" align (.5,.5)
label start:
    "Test"
    return
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
  expect(started.ok()).toBe(true);
  const session = await started
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const document = await request.get('/?session=' + session.session_id);
  expect(document.headers()['cache-control']).toContain('no-store');
  expect(await document.text()).toMatch(/selkies-core-[0-9a-f]+\.js/);
  let oldAliasRequests = 0;
  const sockets: string[] = [];
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/api/websockets') sockets.push(socket.url());
  });
  // Supply a pre-protocol client if the browser requests the old cached alias.
  await page.route('**/selkies-core.js', async (route) => {
    oldAliasRequests++;
    const response = await route.fetch();
    const old = (await response.text()).replace(
      /je\.searchParams\.set\(`vn_connection`[^;]+;je\.searchParams\.set\(`vn_stream_session`[^;]+;/,
      '',
    );
    await route.fulfill({ response, body: old });
  });
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/?session=' + session.session_id);
    await expect
      .poll(() => sockets.some((url) => new URL(url).searchParams.has('vn_connection')))
      .toBe(true);
    await page.waitForFunction(
      () => ((document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth ?? 0) > 0,
    );
    await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
    await page.reload();
    await expect(page.getByRole('button', { name: '菜单', exact: true })).toBeEnabled();
    expect(oldAliasRequests).toBe(0);
    expect(
      (
        await (
          await request.get('/api/v2/session')
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).session_id,
    ).toBe(session.session_id);
  } finally {
    await request.post('/api/v2/session/stop');
  }
});
