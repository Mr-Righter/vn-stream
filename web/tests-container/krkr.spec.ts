import { test, expect } from './fixtures.js';
import { execFileSync } from 'node:child_process';
import { randomUUID, createHash } from 'node:crypto';
import { readFileSync, existsSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const runtimeId = process.env.VN_STREAM_KRKR_RUNTIME;
const automatic = process.env.VN_STREAM_KRKR_AUTO === '1';
test.skip(!runtimeId, 'Requires an isolated verified krkr runtime');

test('krkr streams animation and audio with native input and capability boundaries', async ({
  page,
  context,
  request,
}) => {
  test.setTimeout(90000);
  const name = `Krkr-Probe-${randomUUID().slice(0, 8)}`;
  const root = resolve(process.env.VN_STREAM_KRKR_GAMES ?? '../var/multi-engine-test/games', name);
  execFileSync('python3', [resolve('../tests/fixtures/krkr-smoke/prepare.py'), root]);
  const containerRoot = process.env.VN_STREAM_KRKR_CONTAINER_ROOT ?? '/projects';
  const imported = await request.post('/api/v2/projects', {
    data: { path: `${containerRoot}/${name}` },
  });
  expect(imported.status()).toBe(201);
  const game = await imported
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  expect(game.engine_id).toBe('krkr');
  if (automatic) expect(game.recommendation.runtime_id).toBe(runtimeId);
  expect(game.capabilities.assets).toBe(true);
  for (const key of ['save_management', 'save_editing', 'save_migration', 'native_actions'])
    expect(game.capabilities[key]).toBe(false);
  await context.addInitScript(() => {
    const state = window as Window & { audioProbes: AnalyserNode[] };
    state.audioProbes = [];
    const original = AudioNode.prototype.connect;
    const observed = new WeakSet<AudioNode>();
    AudioNode.prototype.connect = function (destination: AudioNode, ...args: number[]) {
      const result = original.call(this, destination, ...args);
      if (destination === this.context.destination && !observed.has(this)) {
        observed.add(this);
        const analyser = this.context.createAnalyser();
        analyser.fftSize = 2048;
        original.call(this, analyser);
        state.audioProbes.push(analyser);
      }
      return result;
    };
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const started = await request.post(`/api/v2/projects/${game.project_id}/start`, {
    data: automatic ? {} : { runtime_id: runtimeId },
  });
  expect(started.status()).toBe(200);
  try {
    await page.goto('/');
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
          ).readiness,
        {
          timeout: 20000,
        },
      )
      .toBe('ready');
    const video = page.locator('#videoStream');
    const session = await request.get('/api/v2/session').then((r) => r.json());
    const captureWidth = session.data.display.capture[0];
    expect(captureWidth).toBeGreaterThan(0);
    await expect
      .poll(() => video.evaluate((v: HTMLVideoElement) => v.videoWidth))
      .toBe(captureWidth);
    const box = (await video.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect
      .poll(() => {
        const input = existsSync(resolve(root, 'input.txt'))
          ? readFileSync(resolve(root, 'input.txt')).toString('utf16le')
          : '';
        const click = /click:(\d+),(\d+)/.exec(input);
        return click
          ? Math.max(Math.abs(Number(click[1]) - 320), Math.abs(Number(click[2]) - 240))
          : Infinity;
      })
      .toBeLessThanOrEqual(1);
    await page.getByRole('button', { name: '键盘', exact: true }).click();
    await expect(page.locator('#keyboard-input-assist')).toBeFocused();
    await page.keyboard.press('a');
    await expect
      .poll(() => readFileSync(resolve(root, 'input.txt')).toString('utf16le'))
      .toContain('key:65');
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            (window as Window & { audioProbes: AnalyserNode[] }).audioProbes.some((analyser) => {
              const values = new Float32Array(analyser.fftSize);
              analyser.getFloatTimeDomainData(values);
              return (
                analyser.context.state === 'running' && Math.max(...values.map(Math.abs)) > 0.01
              );
            }),
          ),
        { timeout: 15000 },
      )
      .toBe(true)
      .catch(async (error) => {
        console.log(
          'Audio diagnostics',
          await page.evaluate(() =>
            (window as Window & { audioProbes: AnalyserNode[] }).audioProbes.map((a) => {
              const v = new Float32Array(a.fftSize);
              a.getFloatTimeDomainData(v);
              return { state: a.context.state, max: Math.max(...v.map(Math.abs)) };
            }),
          ),
        );
        console.log(
          execFileSync(
            'docker',
            [
              'exec',
              process.env.VN_STREAM_CONTAINER_NAME ?? 'vn-stream-v1-probe',
              'pactl',
              'list',
              'short',
              'sink-inputs',
            ],
            { encoding: 'utf8' },
          ),
        );
        const pcm = execFileSync('docker', [
          'exec',
          process.env.VN_STREAM_CONTAINER_NAME ?? 'vn-stream-v1-probe',
          'ffmpeg',
          '-v',
          'error',
          '-f',
          'pulse',
          '-i',
          'output.monitor',
          '-t',
          '0.5',
          '-f',
          's16le',
          '-ac',
          '1',
          '-ar',
          '48000',
          '-',
        ]);
        let sum = 0;
        for (let i = 0; i < pcm.length; i += 2) sum += pcm.readInt16LE(i) ** 2;
        console.log('Server PCM RMS', Math.sqrt(sum / (pcm.length / 2)));
        console.log(readFileSync(resolve(root, '.vn-stream-session.log'), 'utf8').slice(-1500));
        throw error;
      });
    const sample = () =>
      video.evaluate((v: HTMLVideoElement) => {
        const c = document.createElement('canvas');
        c.width = v.videoWidth;
        c.height = v.videoHeight;
        const ctx = c.getContext('2d')!;
        ctx.drawImage(v, 0, 0);
        const scale = Math.min(v.videoWidth / 640, v.videoHeight / 480);
        const x = (v.videoWidth - 640 * scale) / 2 + 16 * scale;
        const y = (v.videoHeight - 480 * scale) / 2 + 16 * scale;
        return Array.from(ctx.getImageData(x, y, 1, 1).data).slice(0, 3);
      });
    const first = await sample();
    await expect.poll(sample).not.toEqual(first);
    await page.setViewportSize({ width: 844, height: 390 });
    await expect
      .poll(() => video.evaluate((v: HTMLVideoElement) => v.videoWidth))
      .toBe(captureWidth);
    await page.getByRole('button', { name: '游戏设置', exact: true }).click();
    await page.locator('.quit-game').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button', { name: '取消', exact: true }).click();
    expect(
      (
        await (
          await request.get('/api/v2/session')
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
      ).state,
    ).toBe('running');
    await page.getByRole('button', { name: '游戏设置', exact: true }).click();
    await page.locator('.quit-game').click();
    await page.getByRole('dialog').getByRole('button', { name: '确定', exact: true }).click();
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
      )
      .toBe('stopped');
    await expect(page).toHaveURL(/\/library\//);
    const save = resolve(root, 'savedata/probe.ksd');
    const digest = () => createHash('sha256').update(readFileSync(save)).digest('hex');
    const savedDigest = digest();
    writeFileSync(resolve(root, 'load.request'), '');
    const reloaded = await request.post(`/api/v2/projects/${game.project_id}/start`, {
      data: automatic ? {} : { runtime_id: runtimeId },
    });
    expect(reloaded.status()).toBe(200);
    await page.goto('/');
    await expect
      .poll(
        () =>
          page.locator('#videoStream').evaluate((v: HTMLVideoElement) => {
            if (!v.videoWidth) return false;
            const c = document.createElement('canvas');
            c.width = v.videoWidth;
            c.height = v.videoHeight;
            const ctx = c.getContext('2d')!;
            ctx.drawImage(v, 0, 0);
            const [r, g, b] = ctx.getImageData(v.videoWidth / 2, v.videoHeight / 2, 1, 1).data;
            return r < 60 && g > 120 && b < 60;
          }),
        { timeout: 20000 },
      )
      .toBe(true);
    expect(digest()).toBe(savedDigest);
    expect(errors).toEqual([]);
  } finally {
    await request.post('/api/v2/session/stop', { data: {} });
  }
});
