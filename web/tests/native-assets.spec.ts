import { test, expect } from '@playwright/test';
import { signIn } from './fixtures.js';
import { mockEnvelope, engineFixtures, renpyProjectFixture } from './api-fixtures.js';

for (const [engine, width, language, appearance] of [
  ['ons', 320, 'en', 'light'],
  ['krkr', 390, 'zh', 'dark'],
] as const) {
  test(`${engine} exposes media-only assets and waits for prepared image`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await signIn(page);
    await page.addInitScript(
      ({ language, appearance }) =>
        localStorage.setItem('vn-stream:preferences', JSON.stringify({ language, appearance })),
      { language, appearance },
    );
    const id = 'e'.repeat(32);
    const image = Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==',
      'base64',
    );
    let ready = false,
      mediaRequests = 0;
    const engineInfo = engineFixtures.find(({ engine_id }) => engine_id === engine)!;
    const game = renpyProjectFixture({
      project_id: id,
      name: 'Native media',
      engine_id: engine,
      engine_display_name: engineInfo.display_name,
      engine_name_key: engineInfo.engine_name_key,
      engine_version: null,
      path: '/games/native',
      capabilities: {
        assets: true,
        save_management: false,
        save_editing: false,
        save_migration: false,
        native_actions: false,
        exit_mode: 'stop',
        uses_original_engine_version: false,
      },
      recommendation: {
        runtime_id: null,
        build_version: null,
        display_name: null,
        kind: 'unavailable',
      },
    });
    const entry = {
      id: 'picture',
      name: 'image.png',
      path: 'patch.xp3/image.png',
      kind: 'image',
      mime: 'image/webp',
      format: 'WEBP',
      archive: 'patch.xp3',
      requires_preparation: true,
      internal_path: 'image.png',
      size_bytes: 123,
      directory: false,
      preview_supported: true,
    };
    await page.route('**/api/v2/**', async (route) => {
      const url = new URL(route.request().url()),
        path = url.pathname.split('/api/v2/')[1];
      const respond = (data: unknown, status = 200) =>
        route.fulfill({ status, json: mockEnvelope(route.request().url(), data, status) });
      if (path.endsWith('/prepared-content')) {
        mediaRequests++;
        expect(ready).toBe(true);
        return route.fulfill({ body: image, contentType: 'image/png' });
      }
      if (path.endsWith('/thumbnail'))
        return route.fulfill({ body: image, contentType: 'image/png' });
      if (path.endsWith('/prepare'))
        return respond(
          {
            task_id: 'prep',
            state: ready ? 'completed' : 'running',
            progress_percent: ready ? 100 : 35,
            url: ready ? `/api/v2/projects/${id}/assets/picture/prepared-content` : null,
          },
          route.request().method() === 'POST' ? 202 : 200,
        );
      if (path === `projects/${id}/assets/picture`)
        return respond({ ...entry, position: 1, total: 1, previous_id: null, next_id: null });
      if (path === `projects/${id}/assets/entries`) return respond({ entries: [entry], total: 1 });
      if (path === `projects/${id}/assets`)
        return respond({
          ready: true,
          categories: ['image', 'audio', 'video'],
          counts: { image: 1, audio: 0, video: 0 },
          job: { task_id: 'indexed', state: 'completed', phase: 'completed', warnings: 18 },
          warning_details: Array.from({ length: 18 }, (_, i) => ({
            code: 'assets.resourcePreviewUnsupported',
            params: { source: `data.xp3/wtalk/ev101_body${i + 1}.amv`, format: 'AMV' },
          })),
          size_bytes: 123,
          updated_at: null,
        });
      const responses: Record<string, unknown> = {
        projects: [game],
        [`projects/${id}`]: game,
        runtimes: [],
        engines: engineFixtures,
        session: { state: 'stopped' },
        'imports/active': null,
        'uploads/directories/active': null,
      };
      return respond(responses[path] ?? {});
    });
    await page.goto(`/#projects/${id}/assets`);
    await expect(page.getByTestId('asset-category-image')).toBeVisible();
    await expect(page.getByTestId('asset-category-script')).toHaveCount(0);
    await expect(page.getByTestId('asset-category-other')).toHaveCount(0);
    const notices = page.getByTestId('asset-warnings');
    const toggle = notices.getByRole('button', { name: /18/ });
    const warning = notices.getByText(/data.xp3\/wtalk\/ev101_body1.amv/);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(warning).not.toBeVisible();
    expect((await toggle.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(warning).toBeVisible();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(warning).not.toBeVisible();
    await expect(page.getByText(/an archive was not recognized|部分归档无法识别/)).toHaveCount(0);

    await page.goto(`/#projects/${id}/assets/item/picture?category=image&folder=patch.xp3`);
    await expect(page.getByTestId('asset-preparing')).toBeVisible();
    expect(mediaRequests).toBe(0);
    ready = true;
    await expect.poll(() => mediaRequests).toBeGreaterThan(0);
    await expect(page.locator('.vn-file-info')).toContainText('patch.xp3');
    await expect(page.locator('.vn-asset-caption')).toContainText('WEBP');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}
