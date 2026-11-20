import { test, expect } from './fixtures.js';

// These scenarios only read indexes built on disposable real-game copies.
for (const engine of ['ons', 'krkr']) {
  test(`${engine} indexed media renders in a mobile browser`, async ({ page, request }) => {
    test.skip(!process.env.VN_STREAM_NATIVE_ASSETS, 'Requires isolated indexed native samples');
    const projects = (await (await request.get('/api/v2/projects')).json()).data;
    const project = projects.find(
      (p: any) =>
        p.engine_id === engine &&
        p.path.endsWith(engine === 'ons' ? '/ONS-Sample' : '/Krkr-Sample'),
    );
    expect(project).toBeTruthy();
    const prefix = `/api/v2/projects/${project.project_id}/assets`;
    const summary = (await (await request.get(prefix)).json()).data;
    expect(summary.categories).toEqual(['image', 'audio', 'video']);
    await page.setViewportSize({ width: engine === 'ons' ? 320 : 390, height: 844 });
    await page.goto(`/library/#projects/${project.project_id}/assets`);
    const notices = page.getByTestId('asset-warnings');
    if (summary.warning_details?.length) {
      const toggle = notices.getByRole('button');
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await expect(notices.locator('li').first()).not.toBeVisible();
      await toggle.click();
      await expect(notices.locator('li').first()).toBeVisible();
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    } else {
      await expect(notices).toHaveCount(0);
    }

    for (const category of ['image', 'audio']) {
      const items = (
        await (await request.get(`${prefix}/entries?category=${category}&search=.`)).json()
      ).data.entries;
      const item = items.find((entry: any) => !entry.directory);
      expect(item).toBeTruthy();
      await page.goto(
        `/library/#projects/${project.project_id}/assets/item/${item.id}?category=${category}&search=.`,
      );
      if (category === 'image') {
        await expect
          .poll(
            () =>
              page
                .locator('.vn-asset-stage .ant-image-img')
                .evaluateAll((nodes: HTMLImageElement[]) =>
                  nodes.some((node) => node.complete && node.naturalWidth > 0),
                ),
            { timeout: 30000 },
          )
          .toBe(true);
      } else {
        await expect
          .poll(
            () =>
              page
                .locator('.vn-audio-card audio')
                .evaluateAll((nodes: HTMLAudioElement[]) =>
                  nodes.some((node) => Number.isFinite(node.duration) && node.duration > 0),
                ),
            { timeout: 30000 },
          )
          .toBe(true);
      }
      await expect(page.locator('.vn-file-info')).toContainText(item.internal_path);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
    }
  });
}
