import { test, expect } from '@playwright/test';
import { credentials } from './support.js';
test.beforeAll(async ({ request }) => {
  await expect
    .poll(async () => (await request.get('/api/v2/auth/session')).status(), { timeout: 30000 })
    .toBe(401);
});
test('one form login authorizes library, root and media without Basic challenge', async ({
  page,
  context,
  request,
  browser,
  baseURL,
}) => {
  for (const path of ['/api/v2/session', '/api/health', '/api/websockets']) {
    const result = await request.get(path);
    expect(result.status()).toBe(401);
    expect(result.headers()['www-authenticate']).toBeUndefined();
  }
  expect((await request.get('/_session-check')).status()).toBe(404);
  expect((await request.get('/api/v2/auth/verify')).status()).toBe(404);
  await page.goto('/?session=auth-navigation&display_revision=late');
  await expect(page).toHaveURL(/\/library\/login\?/);
  expect(new URL(page.url()).searchParams.get('next')).toBe(
    '/?session=auth-navigation&display_revision=late',
  );
  await page.locator('#username').fill(credentials.username);
  await page.locator('#password').fill(credentials.password);
  // A root page with no game correctly returns to the library after login.
  await page.locator('#login-submit').click();
  await expect(page.locator('h1')).toHaveText('我的游戏');
  const cookie = (await context.cookies()).find((c) => c.name === 'vn_stream_session')!;
  expect(cookie.secure).toBe(new URL(baseURL!).protocol === 'https:');
  expect(cookie.httpOnly && cookie.path === '/').toBeTruthy();
  expect(cookie.expires - Date.now() / 1000).toBeGreaterThan(29 * 86400);
  expect((await context.request.get('/api/health')).status()).toBe(200);
  const reopened = await browser.newContext({
    baseURL,
    storageState: await context.storageState(),
  });
  try {
    expect((await reopened.request.get('/api/v2/auth/session')).status()).toBe(200);
    expect((await reopened.request.get('/api/health')).status()).toBe(200);
  } finally {
    await reopened.close();
  }

  const bad = await context.request.post('/api/v2/projects/refresh');
  expect(bad.status()).toBe(403);
  const csrf = (await context.cookies()).find((c) => c.name === 'vn_stream_csrf')!.value;
  expect(
    (
      await context.request.post('/api/v2/projects/refresh', {
        headers: { 'X-CSRF-Token': csrf, Origin: 'https://foreign.invalid' },
      })
    ).status(),
  ).toBe(403);
  await page.getByRole('button', { name: '管理菜单' }).click();
  await page.locator('#logout').click();
  await expect(page.locator('#login-form')).toBeVisible();
  expect((await context.request.get('/api/health')).status()).toBe(401);
});

test('revoking player login closes media and re-login within60s resumes the same game', async ({
  page,
  playwright,
  baseURL,
}) => {
  const admin = await playwright.request.newContext({ baseURL });
  const login = await admin.post('/api/v2/auth/login', { data: credentials });
  const csrf = {
    'X-CSRF-Token': (
      await login
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
    ).csrf_token,
  };
  try {
    await page.goto('/library/');
    await page.locator('#username').fill(credentials.username);
    await page.locator('#password').fill(credentials.password);
    await page.locator('#login-submit').click();
    await expect(page.locator('h1')).toHaveText('我的游戏');
    const game = await (
      await admin.post('/api/v2/projects', {
        headers: csrf,
        data: { path: '/projects/Stream-Probe' },
      })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    const start = await (
      await admin.post(`/api/v2/projects/${game.project_id}/start`, { headers: csrf, data: {} })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    let closed = false;
    page.on('websocket', (socket) => {
      if (new URL(socket.url()).pathname === '/api/websockets')
        socket.on('close', () => (closed = true));
    });
    await page.goto('/?session=' + start.session_id);
    await page.waitForFunction(
      () => ((document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth ?? 0) > 0,
    );
    const ownCsrf = (await page.context().cookies()).find(
      (c) => c.name === 'vn_stream_csrf',
    )!.value;
    expect(
      (
        await page
          .context()
          .request.post('/api/v2/auth/logout', { headers: { 'X-CSRF-Token': ownCsrf } })
      ).status(),
    ).toBe(200);
    await expect(page.locator('#login-form')).toBeVisible();
    await expect.poll(() => closed).toBe(true);
    const still = await (
      await admin.get('/api/v2/session')
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(still.state).toBe('running');
    expect(still.session_id).toBe(start.session_id);
    expect(still.pid).toBe(start.pid);
    await page.locator('#username').fill(credentials.username);
    await page.locator('#password').fill(credentials.password);
    await page.locator('#login-submit').click();
    await page.waitForFunction(
      () => ((document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth ?? 0) > 0,
    );
    expect(
      (
        await (
          await admin.get('/api/v2/session')
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).session_id,
    ).toBe(start.session_id);
  } finally {
    await admin.post('/api/v2/session/stop', { headers: csrf });
    await admin.dispose();
  }
});
