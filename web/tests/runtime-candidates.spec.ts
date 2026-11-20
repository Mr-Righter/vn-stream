import { test, expect } from './fixtures.js';
import { mockEnvelope, renpyProjectFixture } from './api-fixtures.js';

for (const width of [320, 390, 1024]) {
  test(`runtime names and explicit choices at ${width}px`, async ({ page }) => {
    const project = renpyProjectFixture({
      project_id: 'candidate',
      name: 'ONS sample',
      engine_id: 'ons',
      engine_display_name: 'ONScripter',
      engine_name_key: 'engine.name.ons',
      release_version: '1',
      engine_version: null,
      size_bytes: 1024,
      recommendation: {
        runtime_id: 'ons:yuri:clean',
        build_version: '0.7.7',
        display_name: 'ONScripterYuri 0.7.7',
        kind: 'profile',
      },
      capabilities: {
        assets: true,
        save_management: false,
        save_editing: false,
        save_migration: false,
        native_actions: false,
        exit_mode: 'stop',
        uses_original_engine_version: false,
      },
      runtime_options: [
        {
          runtime_id: 'ons:yuri:clean',
          build_version: '0.7.7',
          display_name: 'ONScripterYuri 0.7.7',
          available: true,
          reason: null,
        },
        {
          runtime_id: 'ons:jh:trial',
          build_version: '0.7.3',
          display_name: 'ONScripter-Jh 0.7.3',
          available: true,
          reason: null,
        },
        {
          runtime_id: 'ons:zero:trial',
          build_version: '0.9.10-development',
          display_name: 'ONScripter-zero 0.9.10-dev',
          available: true,
          reason: null,
        },
        {
          runtime_id: 'ons:new:trial',
          build_version: 'test',
          display_name: 'ONScripter-NEW',
          available: false,
          reason: { code: 'engine.onsNewScript', params: {} },
        },
      ],
    });
    const respond = (path: string, value: unknown) =>
      page.route(`**/api/v2/${path}`, (route) =>
        route.fulfill({ json: mockEnvelope(route.request().url(), value) }),
      );
    await respond('projects', [project]);
    await respond('session', { state: 'stopped' });
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/#projects/candidate');
    const selector = page.getByRole('combobox', { name: '运行器', exact: true });
    await expect(page.locator('.vn-engine-card')).toContainText('ONScripterYuri 0.7.7（推荐）');
    const restrictions = page.locator('.vn-engine-card details');
    const description = restrictions.getByText(/UTF-8/);
    await expect(restrictions.locator('summary')).toHaveText('不可用的运行器');
    await expect(description).toBeHidden();
    const selectBox = (await page.locator('.vn-engine-card .ant-select').boundingBox())!;
    const restrictionsBox = (await restrictions.boundingBox())!;
    expect(restrictionsBox.y - selectBox.y - selectBox.height).toBeCloseTo(16);
    await restrictions.locator('summary').press('Enter');
    await expect(description).toBeVisible();
    await restrictions.locator('summary').press('Enter');
    await expect(description).toBeHidden();
    await selector.click();
    await expect(
      page.getByRole('option', { name: 'ONScripter-Jh 0.7.3', exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('option')).toHaveCount(4);
    await expect(page.getByRole('option', { name: 'ONScripter-NEW', exact: true })).toBeDisabled();
    await expect(page.getByRole('option', { name: /实验性|Experimental/ })).toHaveCount(0);
    await expect(
      page.getByRole('option', { name: 'ONScripter-zero 0.9.10-dev', exact: true }),
    ).toBeVisible();

    await page.getByRole('option', { name: 'ONScripter-zero 0.9.10-dev', exact: true }).click();
    await expect(page.locator('.vn-engine-card')).toContainText('ONScripter-zero 0.9.10-dev');
    await page.locator('#language-toggle').click();
    await page.getByRole('menuitem', { name: 'English', exact: true }).click();
    await expect(page.locator('.vn-engine-card')).toContainText('ONScripter-zero 0.9.10-dev');
    await expect(restrictions.locator('summary')).toHaveText('Unavailable runtimes');
    await restrictions.locator('summary').click();
    await expect(description).toContainText('UTF-8 text script');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await respond('projects', [
      {
        ...project,
        runtime_options: project.runtime_options.map((option) =>
          option.runtime_id === 'ons:new:trial'
            ? { ...option, available: true, reason: null }
            : option,
        ),
      },
    ]);
    await page.reload();
    await expect(restrictions).toHaveCount(0);
    await page.getByRole('combobox', { name: 'Runtime', exact: true }).click();
    await page.getByRole('option', { name: 'ONScripter-NEW', exact: true }).click();
    await expect(page.locator('.vn-engine-card')).toContainText('ONScripter-NEW');
    await respond('projects', [
      {
        ...project,
        engine_id: 'krkr',
        engine_display_name: 'Kirikiri',
        engine_name_key: 'engine.name.krkr',
        recommendation: {
          runtime_id: 'krkr:rs:build',
          build_version: '05ee14ef-modal-20261117',
          display_name: 'krkr-rs',
          kind: 'profile',
        },
        runtime_options: [
          {
            runtime_id: 'krkr:rs:build',
            build_version: '05ee14ef-modal-20261117',
            display_name: 'krkr-rs',
            available: true,
            reason: null,
          },
        ],
      },
    ]);
    await page.reload();
    await expect(page.locator('.vn-engine-card')).toContainText('krkr-rs (Recommended)');
    await expect(page.locator('.vn-engine-card')).not.toContainText('05ee14ef');
  });
}
