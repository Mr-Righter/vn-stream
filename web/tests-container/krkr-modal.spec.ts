import { test, expect } from './fixtures.js';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { copyFileSync, existsSync, readFileSync, unlinkSync } from 'node:fs';
import { resolve } from 'node:path';

test.skip(!process.env.VN_STREAM_KRKR_RUNTIME, 'Requires an isolated verified krkr runtime');

test('modal stays visible until a streamed Layer action returns Yes or No', async ({
  page,
  request,
}) => {
  test.setTimeout(60000);
  const name = `Krkr-Modal-${randomUUID().slice(0, 8)}`;
  const root = resolve(process.env.VN_STREAM_KRKR_GAMES ?? '../var/multi-engine-test/games', name);
  execFileSync('python3', [resolve('../tests/fixtures/krkr-smoke/prepare.py'), root]);
  copyFileSync(resolve('../tests/fixtures/krkr-smoke/modal.tjs'), resolve(root, 'startup.tjs'));
  const imported = await request.post('/api/v2/projects', {
    data: { path: `${process.env.VN_STREAM_KRKR_CONTAINER_ROOT ?? '/projects'}/${name}` },
  });
  expect(imported.status()).toBe(201);
  const project = (await imported.json()).data;
  const started = await request.post(`/api/v2/projects/${project.project_id}/start`, {
    data: { runtime_id: process.env.VN_STREAM_KRKR_RUNTIME },
  });
  expect(started.status()).toBe(200);
  const result = resolve(root, 'result.txt');
  try {
    await page.setViewportSize({ width: 844, height: 390 });
    await page.goto('/');
    const video = page.locator('#videoStream');
    await expect
      .poll(() => video.evaluate((v: HTMLVideoElement) => v.videoWidth))
      .toBeGreaterThan(0);
    const color = () =>
      video.evaluate((v: HTMLVideoElement) => {
        const canvas = document.createElement('canvas');
        canvas.width = v.videoWidth;
        canvas.height = v.videoHeight;
        const ctx = canvas.getContext('2d')!;
        ctx.drawImage(v, 0, 0);
        return Array.from(ctx.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data).slice(
          0,
          3,
        );
      });
    const blue = async () => {
      const c = await color();
      return c[2] > 180 && c[0] < 50 && c[1] < 50;
    };
    const open = async () => {
      await page.getByRole('button', { name: '键盘', exact: true }).click();
      await page.keyboard.press('m');
      await expect.poll(blue).toBe(true);
      expect(existsSync(result)).toBe(false);
    };
    const click = async (x: number, y: number) => {
      const box = (await video.boundingBox())!;
      const size = await video.evaluate((v: HTMLVideoElement) => [v.videoWidth, v.videoHeight]);
      const displayScale = Math.min(box.width / size[0], box.height / size[1]);
      const canvasScale = Math.min(size[0] / 640, size[1] / 480);
      const left = box.x + (box.width - 640 * canvasScale * displayScale) / 2;
      const top = box.y + (box.height - 480 * canvasScale * displayScale) / 2;
      await page.mouse.click(
        left + x * canvasScale * displayScale,
        top + y * canvasScale * displayScale,
      );
    };
    await open();
    await click(410, 200);
    await expect
      .poll(() => (existsSync(result) ? readFileSync(result).toString('utf16le') : ''))
      .toContain('no');
    await expect
      .poll(async () => {
        const c = await color();
        return c[0] > 100 && c[1] < 50 && c[2] < 50;
      })
      .toBe(true);
    unlinkSync(result);
    await open();
    await click(230, 200);
    await expect
      .poll(() => (existsSync(result) ? readFileSync(result).toString('utf16le') : ''))
      .toContain('yes');
    await expect
      .poll(async () => {
        const c = await color();
        return c[1] > 100 && c[0] < 50 && c[2] < 50;
      })
      .toBe(true);
  } finally {
    await request.post('/api/v2/session/stop', { data: {} });
  }
});
