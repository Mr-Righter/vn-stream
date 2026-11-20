import { mockEnvelope, apiData, engineFixtures } from './api-fixtures.js';
import { test, expect } from './fixtures.js';
for (const width of [320, 390, 1024]) {
  test(`native engine details expose only supported capabilities at ${width}px`, async ({
    page,
    request,
  }) => {
    const { mkdir, writeFile } = await import('node:fs/promises');
    const { resolve } = await import('node:path');
    const project = resolve('../var/e2e/inbox', `ONS UI ${width} ${Date.now()}`);
    await mkdir(project, { recursive: true });
    await writeFile(project + '/0.txt', ';$S1280,720\n*define\ngame\n*start\nend\n');
    const response = await request.post('/api/v2/projects', { data: { path: project } });
    expect(response.status()).toBe(201);
    const game = apiData(await response.json());
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/#projects/' + game.project_id);
    await expect(page.locator('#details')).toContainText('ONScripter');
    await expect(page.getByRole('combobox', { name: '运行器', exact: true })).toBeVisible();
    await expect(page.locator('.vn-details-tabs').getByText('素材', { exact: true })).toHaveCount(
      1,
    );
    await expect(page.locator('.vn-details-tabs').getByText('存档', { exact: true })).toHaveCount(
      0,
    );
    await expect(page.getByRole('button', { name: '开始', exact: true })).toBeDisabled();
    expect((await request.get(`/api/v2/projects/${game.project_id}/saves`)).status()).toBe(409);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.goto(`/#projects/${game.project_id}/assets`);
    await expect(page.getByRole('button', { name: '建立素材索引', exact: true })).toBeVisible();
    await expect(page.locator('.vn-details-tabs')).toBeVisible();
  });
}
test('a registered fourth engine uses project labels, capabilities and runtime choices', async ({
  page,
}) => {
  const project = {
    project_id: 'fourth-project',
    name: 'Fourth engine fixture',
    path: '/fixture',
    engine_id: 'fourth',
    engine_display_name: 'Fourth Engine',
    engine_name_key: null,
    engine_version: null,
    release_version: '1',
    icon_url: null,
    size_bytes: 1024,
    last_played_at: null,
    play_seconds: 0,
    available: true,
    runtime_id: null,
    runtime_build_version: null,
    runtime_override: null,
    recommendation: {
      runtime_id: null,
      build_version: null,
      display_name: null,
      kind: 'unavailable',
    },
    capabilities: {
      assets: false,
      save_management: false,
      save_editing: false,
      save_migration: false,
      native_actions: true,
      exit_mode: 'native',
      uses_original_engine_version: false,
    },
    recognition: { status: 'detected', evidence: [], warnings: [] },
    runtime_options: [
      {
        runtime_id: 'fourth:blocked',
        build_version: '1.0',
        display_name: 'Blocked runtime',
        available: false,
        reason: { code: 'engine.runtimeUnavailable', params: {} },
      },
      {
        runtime_id: 'fourth:manual',
        build_version: '2.0',
        display_name: 'Manual runtime',
        available: true,
        reason: null,
      },
    ],
  };
  let globalRuntimeRequests = 0;
  await page.route('**/api/v2/runtimes', (route) => {
    globalRuntimeRequests++;
    return route.abort();
  });
  const respond = (path: string, data: unknown) =>
    page.route(`**/api/v2/${path}`, (route) =>
      route.fulfill({ json: mockEnvelope(route.request().url(), data) }),
    );
  await respond('projects', [project]);
  await respond('session', { state: 'stopped' });
  await respond('projects/fourth-project/start', {
    state: 'running',
    project_id: project.project_id,
  });
  await respond('stream', { available: false, url: null });

  await page.goto('/#projects/fourth-project/saves/item/example');
  await expect(page.locator('#details .badges')).toContainText('Fourth Engine');
  await expect(page.locator('.vn-details-tabs').getByRole('radio')).toHaveCount(1);
  const start = page.getByRole('button', { name: '开始', exact: true });
  await expect(start).toBeDisabled();
  await page.getByRole('combobox', { name: '运行器', exact: true }).click();
  await expect(page.getByRole('option', { name: 'Blocked runtime' })).toBeDisabled();
  await page.getByRole('option', { name: 'Manual runtime' }).click();
  const posted = page.waitForRequest('**/api/v2/projects/fourth-project/start');
  await start.click();
  expect((await posted).postDataJSON()).toEqual({ runtime_id: 'fourth:manual' });
  expect(globalRuntimeRequests).toBe(0);
});

test('native player hides unsupported actions and confirms stopping before dispatch', async ({
  page,
}) => {
  const sent: any[] = [];
  await page.route('**/native-tools?*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<html><body><input id="keyboard-input-assist"><script type="module" src="/game-controls.js"></script></body></html>',
    }),
  );
  await page.route('**/api/v2/session', (route) =>
    route.fulfill({
      json: mockEnvelope(
        route.request().url(),
        { state: 'running', session_id: 'b'.repeat(32), engine_id: 'ons' },
        200,
      ),
    }),
  );
  await page.route('**/api/v2/auth/session', (route) =>
    route.fulfill({ json: mockEnvelope(route.request().url(), { csrf_token: 'test-csrf' }, 200) }),
  );
  await page.routeWebSocket('**/api/v2/session/control', (ws) =>
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
            supported: ['stop'],
            available: ['stop'],
          }),
        );
    }),
  );
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto('/native-tools?session=' + 'b'.repeat(32));
  await expect(page.getByRole('button', { name: '键盘', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '存档', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '游戏设置', exact: true }).click();
  await page.locator('.quit-game').click();
  await expect(page.getByRole('dialog')).toContainText('尚未保存');
  expect(sent.filter((m) => m.action === 'stop')).toHaveLength(0);
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '游戏设置', exact: true }).click();
  await page.locator('.quit-game').click();
  await page.getByRole('dialog').getByRole('button', { name: '确定', exact: true }).click();
  await expect.poll(() => sent.filter((m) => m.action === 'stop').length).toBe(1);
});
