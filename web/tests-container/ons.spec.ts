import { test, expect } from './fixtures.js';
import { createHash, randomUUID } from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const runtimeId = process.env.VN_STREAM_ONS_RUNTIME;
test.skip(!runtimeId, 'Requires an isolated verified ONS runtime');

test('ONS streams its native canvas and reloads an unchanged authored save', async ({
  page,
  request,
}) => {
  const name = `ONS-Probe-${randomUUID().slice(0, 8)}`;
  const inbox = resolve(process.env.VN_STREAM_ONS_GAMES!);
  const root = resolve(inbox, name);
  mkdirSync(root);
  copyFileSync(resolve('../tests/fixtures/ons-smoke/0.txt'), resolve(root, '0.txt'));
  copyFileSync(resolve(inbox, 'ONS-Sample/default.ttf'), resolve(root, 'default.ttf'));
  const imported = await request.post('/api/v2/projects', { data: { path: `/projects/${name}` } });
  expect(imported.status()).toBe(201);
  const { data: project } = await imported.json();
  expect(project.engine_id).toBe('ons');
  expect(project.capabilities.save_editing).toBe(false);
  expect(project.capabilities.native_actions).toBe(false);
  const greenFrame = () =>
    page.locator('#videoStream').evaluate((video: HTMLVideoElement) => {
      if (!video.videoWidth) return false;
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const context = canvas.getContext('2d')!;
      context.drawImage(video, 0, 0);
      const [r, g, b] = context.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data;
      return r < 60 && g > 120 && b < 60;
    });
  const start = async () => {
    const response = await request.post(`/api/v2/projects/${project.project_id}/start`, {
      data: { runtime_id: runtimeId },
    });
    expect(response.status()).toBe(200);
    await page.goto('/');
    await expect.poll(greenFrame, { timeout: 20000 }).toBe(true);
    await expect
      .poll(() =>
        page
          .locator('#videoStream')
          .evaluate((video: HTMLVideoElement) => [video.videoWidth, video.videoHeight]),
      )
      .toEqual([640, 480]);
  };
  const revision = () =>
    createHash('sha256')
      .update(readFileSync(resolve(root, 'save/save1.dat')))
      .digest('hex');
  try {
    await start();
    const saved = revision();
    await request.post('/api/v2/session/stop', { data: {} });
    await expect(page).toHaveURL(/\/library\//);
    writeFileSync(resolve(root, 'load.request'), '');
    await start();
    expect(revision()).toBe(saved);
    expect((await request.get(`/api/v2/projects/${project.project_id}/saves`)).status()).toBe(409);
  } finally {
    await request.post('/api/v2/session/stop', { data: {} });
  }
});
