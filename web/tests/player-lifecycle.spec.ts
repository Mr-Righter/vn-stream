import { test, expect, type Page, type WebSocketRoute } from './fixtures.js';
import { readFileSync } from 'node:fs';

const sessionId = 'a'.repeat(32);
const playerUrl = `/player-lifecycle?session=${sessionId}`;
const control = JSON.parse(
  readFileSync(new URL('../../tests/fixtures/player-control.json', import.meta.url), 'utf8'),
);
const status = control.ready;

async function mockPlayer(page: Page) {
  await page.route('**/player-lifecycle?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<html><body><script type="module" src="/game-controls.js"></script></body></html>',
    }),
  );
}

test('one session request serves the toolbar and reconnects control without overlapping polls', async ({
  page,
}) => {
  await mockPlayer(page);
  let release!: () => void;
  const blocked = new Promise<void>((resolve) => (release = resolve));
  let requests = 0;
  await page.route('**/api/v2/session', async (route) => {
    requests++;
    await blocked;
    await route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } });
  });
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket('**/api/v2/session/control', (socket) => {
    sockets.push(socket);
    socket.onMessage((data) => {
      if (JSON.parse(String(data)).type === 'hello') socket.send(JSON.stringify(status));
    });
  });
  await page.goto(playerUrl);
  await expect.poll(() => requests).toBeGreaterThan(0);
  try {
    await page.waitForTimeout(1150);
    expect(requests).toBe(1);
    expect(sockets).toHaveLength(0);
  } finally {
    release();
  }
  const save = page.getByRole('button', { name: '存档', exact: true });
  await expect(save).toBeEnabled();
  expect(sockets).toHaveLength(1);
  sockets[0].close();
  await expect(save).toBeDisabled();
  await expect(save).toBeEnabled();
  expect(sockets).toHaveLength(2);
});

test('malformed control messages leave the last valid capabilities and authority in place', async ({
  page,
}) => {
  await mockPlayer(page);
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } }),
  );
  const sent: string[] = [];
  let socket: WebSocketRoute;
  await page.routeWebSocket('**/api/v2/session/control', (route) => {
    socket = route;
    socket.onMessage((data) => {
      const message = JSON.parse(String(data));
      if (message.type === 'hello') route.send(JSON.stringify(status));
      if (message.type === 'action') sent.push(message.action);
    });
  });
  const errors: Error[] = [];
  page.on('pageerror', (error) => errors.push(error));
  await page.goto(playerUrl);
  const save = page.getByRole('button', { name: '存档', exact: true });
  await expect(save).toBeEnabled();
  socket!.send('{');
  socket!.send('null');
  socket!.send(JSON.stringify({ ...status, controller: 'true', available: null }));
  socket!.send(JSON.stringify({ type: 'action', state: 'rejected', request_id: 42 }));
  await save.click();
  await expect.poll(() => sent).toEqual(['save']);
  expect(errors).toEqual([]);
  await expect(page.locator('.game-control-notice')).toHaveCount(0);
});

test('pagehide closes control and pageshow rechecks the session before reconnecting', async ({
  page,
}) => {
  await mockPlayer(page);
  let requests = 0;
  await page.route('**/api/v2/session', (route) => {
    requests++;
    return route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } });
  });
  let sockets = 0;
  let closes = 0;
  await page.routeWebSocket('**/api/v2/session/control', (socket) => {
    sockets++;
    socket.onClose(() => closes++);
    socket.onMessage((data) => {
      if (JSON.parse(String(data)).type === 'hello') socket.send(JSON.stringify(status));
    });
  });
  await page.goto(playerUrl);
  const save = page.getByRole('button', { name: '存档', exact: true });
  await expect(save).toBeEnabled();
  await page.evaluate(() =>
    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })),
  );
  await expect.poll(() => closes).toBe(1);
  await expect(save).toBeDisabled();
  const before = requests;
  await page.waitForTimeout(1150);
  expect(requests).toBe(before);
  expect(sockets).toBe(1);
  await page.evaluate(() =>
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })),
  );
  await expect(save).toBeEnabled({ timeout: 2000 });
  expect(requests).toBeGreaterThan(before);
  expect(sockets).toBe(2);
});

test('hidden pages pause heartbeats while the current media epoch resumes immediately', async ({
  page,
}) => {
  await mockPlayer(page);
  await page.clock.install();
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } }),
  );
  let socket: WebSocketRoute;
  const heartbeats: string[] = [];
  await page.routeWebSocket('**/api/v2/session/control', (route) => {
    socket = route;
    route.onMessage((data) => {
      const frame = JSON.parse(String(data));
      if (frame.type === 'hello') route.send(JSON.stringify(control.waiting));
      if (frame.type === 'heartbeat') heartbeats.push(frame.epoch);
    });
  });
  await page.goto(playerUrl);
  const save = page.getByRole('button', { name: '存档', exact: true });
  await expect(save).toBeDisabled();
  expect(heartbeats).toHaveLength(0);
  socket!.send(JSON.stringify(status));
  await expect.poll(() => heartbeats).toEqual([status.epoch]);
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.clock.runFor(6500);
  expect(heartbeats).toEqual([status.epoch]);
  socket!.send(JSON.stringify({ ...status, epoch: 'restored-epoch', skip: true }));
  await expect(page.getByRole('button', { name: '快进', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(() => heartbeats).toEqual([status.epoch, 'restored-epoch']);
});

test('control takeover persists across restoration of the same page', async ({ page }) => {
  await mockPlayer(page);
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } }),
  );
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket('**/api/v2/session/control', (socket) => {
    sockets.push(socket);
    socket.onMessage((data) => {
      if (JSON.parse(String(data)).type === 'hello') socket.send(JSON.stringify(status));
    });
  });
  await page.goto(playerUrl);
  const save = page.getByRole('button', { name: '存档', exact: true });
  await expect(save).toBeEnabled();
  sockets[0].send(JSON.stringify(control.superseded));
  await expect(save).toBeDisabled();
  await expect(page.locator('.game-control-notice')).toContainText('接管');
  const restored = page.waitForResponse('**/api/v2/session');
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
  });
  await restored;
  await page.waitForTimeout(1150);
  expect(sockets).toHaveLength(1);
  await expect(save).toBeDisabled();
});

test('a delayed terminal response from before page hide cannot end the restored player', async ({
  page,
}) => {
  await mockPlayer(page);
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    let seen = false;
    window.fetch = (resource, options) => {
      if (String(resource).endsWith('/api/v2/session') && !seen) {
        seen = true;
        // Simulate a request that completes despite the cancellation signal.
        return fetch(resource, { ...options, signal: undefined });
      }
      return fetch(resource, options);
    };
  });
  let release!: () => void;
  const blocked = new Promise<void>((resolve) => (release = resolve));
  let requests = 0;
  await page.route('**/api/v2/session', async (route) => {
    if (++requests === 1) {
      await blocked;
      await route.fulfill({ json: { data: { state: 'stopped' } } });
    } else await route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } });
  });
  await page.routeWebSocket('**/api/v2/session/control', (socket) => {
    socket.onMessage((data) => {
      if (JSON.parse(String(data)).type === 'hello') socket.send(JSON.stringify(status));
    });
  });
  await page.goto(playerUrl);
  await expect.poll(() => requests).toBe(1);
  try {
    await page.evaluate(() => {
      window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    await expect(page.getByRole('button', { name: '存档', exact: true })).toBeEnabled();
  } finally {
    release();
  }
  await page.waitForTimeout(300);
  await expect(page).toHaveURL(playerUrl);
  await expect(page.getByRole('button', { name: '存档', exact: true })).toBeEnabled();
});

test('only rejection of the current quit request clears the pending exit state', async ({
  page,
}) => {
  await mockPlayer(page);
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({ json: { data: { state: 'running', session_id: sessionId } } }),
  );
  let socket: WebSocketRoute;
  let quitId: string;
  await page.routeWebSocket('**/api/v2/session/control', (route) => {
    socket = route;
    route.onMessage((data) => {
      const frame = JSON.parse(String(data));
      if (frame.type === 'hello') route.send(JSON.stringify(status));
      if (frame.type === 'action' && frame.action === 'quit') quitId = frame.request_id;
    });
  });
  await page.goto(playerUrl);
  const settings = page.getByRole('button', { name: '游戏设置', exact: true });
  const quit = page.getByRole('button', { name: '退出游戏', exact: true });
  await settings.click();
  await quit.click();
  await expect.poll(() => quitId).toMatch(/^[a-f0-9]{32}$/);
  await settings.click();
  await expect(quit).toBeDisabled();
  socket!.send(JSON.stringify(control.rejected));
  socket!.send(JSON.stringify({ ...control.rejected, request_id: quitId!, epoch: 'stale' }));
  socket!.send(JSON.stringify({ ...status, skip: true }));
  await expect(page.getByRole('button', { name: '快进', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(quit).toBeDisabled();
  await expect(page.locator('.game-control-notice')).toHaveCount(0);
  socket!.send(JSON.stringify({ ...control.rejected, request_id: quitId! }));
  await expect(quit).toBeEnabled();
  await expect(page.locator('.game-control-notice')).toBeVisible();
});

test('a control end waits for a confirmed HTTP session end before returning to the library', async ({
  page,
}) => {
  await mockPlayer(page);
  let result = 'running';
  await page.route('**/api/v2/session', (route) =>
    result === 'unavailable'
      ? route.abort()
      : route.fulfill({ json: { data: { state: result, session_id: sessionId } } }),
  );
  await page.route('**/library/', (route) => route.fulfill({ body: 'Library' }));
  let socket: WebSocketRoute;
  await page.routeWebSocket('**/api/v2/session/control', (route) => {
    socket = route;
    route.onMessage((data) => {
      if (JSON.parse(String(data)).type === 'hello') route.send(JSON.stringify(status));
    });
  });
  await page.goto(playerUrl);
  const save = page.getByRole('button', { name: '存档', exact: true });
  await expect(save).toBeEnabled();
  result = 'unavailable';
  socket!.send(JSON.stringify(control.ended));
  await expect(save).toBeDisabled();
  await expect(page.getByRole('status')).toContainText('正在重试');
  await expect(page).toHaveURL(playerUrl);
  result = 'stopped';
  await expect(page).toHaveURL('/library/');
  expect(
    await page.evaluate(() => JSON.parse(sessionStorage.getItem('vn-stream:return')!)),
  ).toEqual({
    code: 'ui.gameExited',
    params: {},
  });
});
