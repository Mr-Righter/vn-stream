import { mockEnvelope, apiData, engineFixtures, renpyProjectFixture } from './api-fixtures.js';
import { test, expect, type Page } from '@playwright/test';
import { signIn } from './fixtures.js';
const game = renpyProjectFixture({
  project_id: 'd'.repeat(32),
  name: 'Layout fixture',
  path: '/projects/fixture',
  engine_version: '8.3.2',
  release_version: '1.0',
  size_bytes: 4096,
  recommendation: {
    build_version: '8.3.2',
    kind: 'exact',
    runtime_id: 'renpy:8.3.2',
    display_name: null,
  },
});
const image = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAALUlEQVR4nGPUydjHQEvARFPTRy0YtWDUglELRi0YtWDUglELRi0YtWDUAioCAJApAZIYbZpJAAAAAElFTkSuQmCC',
  'base64',
);
function wave() {
  const data = Buffer.alloc(44 + 8000 * 30, 128);
  data.write('RIFF');
  data.writeUInt32LE(data.length - 8, 4);
  data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(8000, 24);
  data.writeUInt32LE(8000, 28);
  data.writeUInt16LE(1, 32);
  data.writeUInt16LE(8, 34);
  data.write('data', 36);
  data.writeUInt32LE(data.length - 44, 40);
  return data;
}
async function prepare(page: Page) {
  await signIn(page);
  const stats = { active: 0, maximum: 0, requests: 0 };
  const files = Array.from({ length: 105 }, (_, i) => ({
    id: `img-${i}`,
    name: `Image-${String(i).padStart(3, '0')}.png`,
    path: `gui/${String(i).padStart(3, '0')}.png`,
    directory: false,
    kind: 'image',
    mime: 'image/png',
    size_bytes: 1024,
  }));
  const videos = Array.from({ length: 8 }, (_, i) => ({
    id: `video-${i}`,
    name: `clip-${i}.webm`,
    path: `clip-${i}.webm`,
    directory: false,
    kind: 'video',
    mime: 'video/webm',
    size_bytes: 1024 * 1024,
  }));
  const audio = {
    id: 'audio',
    name: 'voice.wav',
    path: 'voice.wav',
    directory: false,
    kind: 'audio',
    mime: 'audio/wav',
    size_bytes: 240044,
  };
  await page.route('**/api/v2/**', async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname.split('/api/v2/')[1];
    if (path.endsWith('/thumbnail')) {
      stats.active++;
      stats.requests++;
      stats.maximum = Math.max(stats.maximum, stats.active);
      await new Promise((resolve) => setTimeout(resolve, 40));
      stats.active--;
      return route.fulfill({ body: image, contentType: 'image/png' });
    }
    if (path.endsWith('/assets/audio/content')) {
      const data = wave(),
        range = route.request().headers()['range'];
      const match = range?.match(/bytes=(\d+)-(\d*)/);
      if (match) {
        const start = Number(match[1]),
          end = match[2] ? Math.min(Number(match[2]), data.length - 1) : data.length - 1;
        return route.fulfill({
          status: 206,
          body: data.subarray(start, end + 1),
          contentType: 'audio/wav',
          headers: {
            'Accept-Ranges': 'bytes',
            'Content-Range': `bytes ${start}-${end}/${data.length}`,
          },
        });
      }
      return route.fulfill({
        body: data,
        contentType: 'audio/wav',
        headers: { 'Accept-Ranges': 'bytes' },
      });
    }
    if (path.endsWith('/content')) return route.fulfill({ body: image, contentType: 'image/png' });
    const responses: Record<string, unknown> = {
      projects: [game],

      session: { state: 'stopped' },
      'imports/active': null,
      'uploads/directories/active': null,
      engines: engineFixtures,
    };
    if (path === `projects/${game.project_id}/assets`)
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            ready: true,
            generation: 'fixture',
            counts: { image: 105, video: 8, audio: 1, script: 0, other: 0 },
            size_bytes: 4096,
            updated_at: new Date(1 * 1000).toISOString(),
            job: null,
          },
          200,
        ),
      });
    if (path.endsWith('/assets/entries')) {
      const category = url.searchParams.get('category'),
        folder = url.searchParams.get('folder'),
        p = Number(url.searchParams.get('page') || 1);
      const entries =
        category === 'audio'
          ? [audio]
          : category === 'video'
            ? videos
            : folder === 'gui'
              ? files
              : [{ id: 'folder:gui', path: 'gui', name: 'gui', directory: true, count: 105 }];
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          { entries: entries.slice((p - 1) * 100, p * 100), total: entries.length },
          200,
        ),
      });
    }
    const uid = path.split('/').at(-1);
    const entry = [...files, ...videos, audio].find((f) => f.id === uid);
    if (entry) {
      const siblings = entry.kind === 'image' ? files : entry.kind === 'video' ? videos : [audio],
        index = siblings.findIndex((f) => f.id === uid);
      return route.fulfill({
        json: mockEnvelope(
          route.request().url(),
          {
            ...entry,
            position: index + 1,
            total: siblings.length,
            previous_id: siblings[index - 1]?.id ?? null,
            next_id: siblings[index + 1]?.id ?? null,
          },
          200,
        ),
      });
    }
    return route.fulfill({
      json: mockEnvelope(route.request().url(), responses[path] ?? null, 200),
    });
  });
  await page.goto(`/#projects/${game.project_id}`);
  return stats;
}
for (const width of [320, 390, 1024])
  test(`compact rows and aligned path at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await prepare(page);
    await page.getByText('素材', { exact: true }).click();
    await page.getByTestId('asset-category-image').click();
    await page.getByTestId('asset-row-folder:gui').click();
    await expect(page.getByTestId('asset-row-img-0').locator('img')).toBeVisible();
    const centers = await page
      .locator('.vn-content-path > svg,.vn-crumb button,.vn-crumb-separator')
      .evaluateAll((nodes) =>
        nodes.map((node) => {
          const r = node.getBoundingClientRect();
          return r.y + r.height / 2;
        }),
      );
    expect(Math.max(...centers) - Math.min(...centers)).toBeLessThanOrEqual(1);
    expect(
      await page
        .getByTestId('asset-row-img-0')
        .evaluate((node) => !!node.querySelector('.vn-row-chevron')),
    ).toBeTruthy();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    ).toBeTruthy();
    await page.getByTestId('asset-row-img-0').click();
    await expect(page.locator('.hero')).toBeHidden();
    await expect(page.locator('.vn-asset-detail h2')).toHaveText('Image-000.png');
    await page.locator('#back').click();
    await expect(page.getByTestId('asset-row-img-0')).toBeVisible();
    await page.screenshot({
      path: `../var/content-layout-work/media-list-${width}.png`,
      fullPage: false,
    });
  });
test('deep detail reload and cross-page navigation retain browsing context', async ({ page }) => {
  await prepare(page);
  await page.goto(
    `/#projects/${game.project_id}/assets/item/img-99?category=image&folder=gui&page=1`,
  );
  await expect(page.getByText('100 / 105', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '下一个', exact: true }).click();
  await expect(page.getByText('101 / 105', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('.vn-asset-detail h2')).toHaveText('Image-100.png');
  await page.locator('#back').click();
  await expect(page).toHaveURL(/assets\/browse\?category=image&folder=gui&page=1/);
});
test('video posters are lazy and use at most three concurrent requests', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const stats = await prepare(page);
  await page.getByText('素材', { exact: true }).click();
  await page.getByTestId('asset-category-video').click();
  await expect(page.locator('[data-thumbnail-state="ready"]').first()).toBeVisible();
  expect(stats.maximum).toBeLessThanOrEqual(3);
  expect(await page.locator('.vn-video-mark').count()).toBe(8);
});
test('custom audio controls seek, loop and stop on return', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await prepare(page);
  await page.goto(`/#projects/${game.project_id}/assets/item/audio?category=audio`);
  await expect(page.locator('audio')).toHaveJSProperty('readyState', 4);
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '单曲循环', exact: true }).click();
  await expect(page.locator('audio')).toHaveJSProperty('loop', true);
  await page.getByRole('button', { name: '前进10秒', exact: true }).click();
  await expect
    .poll(() => page.locator('audio').evaluate((node: HTMLAudioElement) => node.currentTime))
    .toBeGreaterThanOrEqual(10);
  await page.locator('audio').evaluate((node) => {
    (
      window as unknown as {
        oldAudio: HTMLAudioElement;
      }
    ).oldAudio = node as HTMLAudioElement;
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
  await page.locator('#back').click();
  await expect(page.locator('.vn-asset-detail')).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (
            window as unknown as {
              oldAudio: HTMLAudioElement;
            }
          ).oldAudio.paused,
      ),
    )
    .toBeTruthy();
});
test('top-level project tabs retain their position through media loading', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 840 });
  await prepare(page);
  let onMediaRequested!: () => void;
  const requested = new Promise<void>((resolve) => (onMediaRequested = resolve));
  let release!: () => void;
  const loading = new Promise<void>((resolve) => (release = resolve));
  await page.route(`**/api/v2/projects/${game.project_id}/assets`, async (route) => {
    onMediaRequested();
    await loading;
    return route.fallback();
  });
  await page.addStyleTag({ content: '#details .vn-project-overview { min-height: 1100px; }' });
  const mediaHeight = await page.addStyleTag({
    content: '#details > section.vn-content-page { min-height: 1100px; }',
  });
  const tabs = page.locator('.vn-details-tabs');
  try {
    await tabs.evaluate((element) =>
      scrollTo(0, element.getBoundingClientRect().top + scrollY - 95),
    );
    const before = await page.evaluate(() => scrollY);
    expect(before).toBeGreaterThan(50);
    await tabs.getByText('素材', { exact: true }).click();
    await requested;
    await expect(page).toHaveURL(/\/assets$/);
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(before);
    release();
    await expect(page.getByTestId('asset-category-image')).toBeVisible();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(before);
    await tabs.getByText('信息', { exact: true }).click();
    await expect(page.locator('.vn-project-overview')).toBeVisible();
    await expect.poll(() => page.evaluate(() => scrollY)).toBe(before);
    await mediaHeight.evaluate((element) => element.remove());
    await page.setViewportSize({ width: 390, height: 1200 });
    await tabs.getByText('素材', { exact: true }).click();
    await expect(page.getByTestId('asset-category-image')).toBeVisible();
    expect(await page.evaluate(() => scrollY)).toBe(0);
    await tabs.getByText('信息', { exact: true }).click();
    await expect(page.locator('.vn-project-overview')).toBeVisible();
    expect(await page.evaluate(() => scrollY)).toBe(0);
  } finally {
    release();
  }
});

test('browser back and forward restore the scrolled media list', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepare(page);
  await page.goto(`/#projects/${game.project_id}/assets/browse?category=image&folder=gui`);
  await page.getByTestId('asset-row-img-50').scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  await page.getByTestId('asset-row-img-50').click();
  await expect(page.locator('.vn-asset-detail h2')).toHaveText('Image-050.png');
  await page.goBack();
  await expect(page.getByTestId('asset-row-img-50')).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before - 3);
  await page.getByTestId('asset-row-img-75').scrollIntoViewIfNeeded();
  const later = await page.evaluate(() => window.scrollY);
  expect(later).toBeGreaterThan(before + 100);
  await page.goForward();
  await expect(page.locator('.vn-asset-detail h2')).toHaveText('Image-050.png');
  await page.goBack();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(later - 3);
});
