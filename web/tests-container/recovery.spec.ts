import { test, expect } from './fixtures.js';
import { existsSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { events, headers, probe } from './support.js';

test('a dropped media connection keeps the game and accepts input after reconnect', async ({
  page,
  request,
}, testInfo) => {
  const prior = await (
    await request.get('/api/v2/session', { headers })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  expect(['stopped', 'failed']).toContain(prior.state);
  const imported = await request.post('/api/v2/projects', {
    headers,
    data: { path: '/projects/Stream-Probe' },
  });
  expect(imported.status()).toBe(201);
  const game = await imported
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const baseline = events().length;
  const started = await request.post(`/api/v2/projects/${game.project_id}/start`, {
    headers,
    data: {},
  });
  expect(started.ok()).toBe(true);
  const session = await started
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  let disconnected = false;
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/api/websockets') {
      socket.on('close', () => {
        disconnected = true;
      });
    }
  });
  try {
    await expect
      .poll(() =>
        events()
          .slice(baseline)
          .some((row) => row.event === 'main_menu'),
      )
      .toBe(true);
    await page.goto('/');
    await page.waitForFunction(
      () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
    );
    const box = (await page.locator('#videoStream').boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.keyboard.press('Enter');
    await expect
      .poll(() =>
        events()
          .slice(baseline)
          .some((row) => row.event === 'ready'),
      )
      .toBe(true);
    // The pinned client moves WebSocket I/O into a worker; use its public transport handle.
    await page.evaluate(() => {
      const client = window as Window & {
        selkiesTransport: { close: (code: number, reason: string) => void };
      };
      client.selkiesTransport.close(1012, 'verification transport interruption');
    });
    await expect.poll(() => disconnected).toBe(true);
    const current = await (
      await request.get('/api/v2/session', { headers })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(current.state).toBe('running');
    expect(current.pid).toBe(session.pid);
    const before = events().length;
    await page.reload();
    await page.waitForFunction(
      () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
    );
    const fresh = (await page.locator('#videoStream').boundingBox())!;
    await page.mouse.click(fresh.x + fresh.width / 2, fresh.y + fresh.height / 2);
    const screenshot = resolve(probe, 'frame.png');
    const revision = () =>
      existsSync(screenshot) ? statSync(screenshot, { bigint: true }).mtimeNs.toString() : '';
    const oldRevision = revision();
    // A new engine-side screenshot proves input reached the existing game after recovery.
    await page.keyboard.press('p');
    await expect.poll(revision).not.toBe(oldRevision);
    expect(revision()).not.toBe('');
    expect(
      events()
        .slice(before)
        .filter((row) => ['main_menu', 'loaded'].includes(String(row.event))),
    ).toHaveLength(0);
    await testInfo.attach('reconnected-game', {
      body: await page.locator('#videoStream').screenshot(),
      contentType: 'image/png',
    });
  } finally {
    await request.post('/api/v2/session/stop', { headers, data: {} });
  }
});
