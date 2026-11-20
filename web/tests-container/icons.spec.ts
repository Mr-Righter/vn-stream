import { test, expect } from './fixtures.js';
import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { probe } from './support.js';

test('project icon resolves from the deployed library and detail page', async ({
  page,
  request,
}) => {
  const name = `Icon-Probe-${randomUUID().slice(0, 8)}`;
  const root = resolve(dirname(probe), name);
  mkdirSync(resolve(root, 'game/gui'), { recursive: true });
  writeFileSync(resolve(root, 'game/script.rpy'), 'label start:\n    return\n');
  execFileSync(resolve('../.venv/bin/python'), [
    '-c',
    'from PIL import Image; import sys; Image.new("RGB", (64,64), "#2563eb").save(sys.argv[1])',
    resolve(root, 'game/gui/window_icon.png'),
  ]);
  const response = await request.post('/api/v2/projects', { data: { path: `/projects/${name}` } });
  expect(response.status()).toBe(201);
  const project = (await response.json()).data;
  try {
    expect(project.icon_url).toMatch(/^\/api\/v2\/projects\//);
    const icon = await request.get(project.icon_url);
    expect(icon.status()).toBe(200);
    expect(icon.headers()['content-type']).toBe('image/png');
    await page.goto('/library/');
    const image = page.locator(`.game-icon img[src*="${project.project_id}/icon"]`);
    await expect
      .poll(() => image.first().evaluate((img: HTMLImageElement) => img.naturalWidth))
      .toBe(64);
    await page.goto('/library/#projects/' + project.project_id);
    await expect
      .poll(() =>
        page
          .locator('#details .game-icon img')
          .evaluate((img: HTMLImageElement) => img.naturalWidth),
      )
      .toBe(64);
  } finally {
    const deletion = await request.delete('/api/v2/projects/' + project.project_id);
    expect(deletion.status()).toBe(202);
    const task = (await deletion.json()).data;
    await expect
      .poll(
        async () =>
          (await (await request.get('/api/v2/tasks/project-deletions/' + task.task_id)).json()).data
            .state,
      )
      .toBe('completed');
  }
});
