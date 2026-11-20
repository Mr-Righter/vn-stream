import { signIn } from './fixtures.js';
import { test, expect, type Page } from './fixtures.js';
import { createHash, randomUUID } from 'node:crypto';
import { copyFileSync, existsSync, readFileSync, readdirSync, statSync, unlinkSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { events as readEvents, headers, probe, settings } from './support.js';

function saveRevision(project = probe) {
  const directory = resolve(project, 'game/saves');
  if (!existsSync(directory)) return '';
  return readdirSync(directory)
    .filter((name) => /^smoke-1.*\.save$/.test(name))
    .sort()
    .map((name) => `${name}:${statSync(resolve(directory, name), { bigint: true }).mtimeNs}`)
    .join('|');
}

async function frameSample(page: Page): Promise<number[]> {
  return page.locator('#videoStream').evaluate((video: HTMLVideoElement) => {
    const canvas = document.createElement('canvas');
    canvas.width = 160;
    canvas.height = 90;
    const context = canvas.getContext('2d')!;
    context.imageSmoothingEnabled = false;
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const rgb = [];
    for (let i = 0; i < rgba.length; i += 4) rgb.push(rgba[i], rgba[i + 1], rgba[i + 2]);
    return rgb;
  });
}

function primaryFractions(rgb: number[]): number[] {
  const counts = [0, 0, 0];
  for (let i = 0; i < rgb.length; i += 3) {
    for (let channel = 0; channel < 3; channel++) {
      if (
        rgb[i + channel] > 120 &&
        rgb[i + ((channel + 1) % 3)] < 80 &&
        rgb[i + ((channel + 2) % 3)] < 80
      )
        counts[channel]++;
    }
  }
  return counts.map((count) => count / (rgb.length / 3));
}

const mediaCases = [
  { version: '8.5.3', kind: 'directory' },
  ...['7.8.2', '8.5.3'].flatMap((version) => ['zip', 'apk'].map((kind) => ({ version, kind }))),
];
for (const sample of mediaCases) {
  test(`${sample.version} ${sample.kind}: media, saving and reconnect use the same game`, async ({
    page,
    context,
    request,
  }, testInfo) => {
    test.skip(
      sample.kind !== 'directory' && process.env.VN_STREAM_PACKAGED_MEDIA !== '1',
      'opt-in: prepare package fixtures first',
    );
    const prior = await (
      await request.get('/api/v2/session', { headers })
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(['stopped', 'failed']).toContain(prior.state);
    let source = '/projects/Stream-Probe';
    let packageCopy: string | undefined;
    let originalPackage: string | undefined;
    let packageHash: string | undefined;
    let startingGame: string | undefined;
    try {
      if (sample.kind !== 'directory') {
        const manifest =
          process.env.VN_STREAM_CONTAINER_PACKAGES ?? resolve(probe, '../../packages.json');
        const cases = JSON.parse(readFileSync(manifest, 'utf8'));
        const item = cases.find(
          (row: { version: string; kind: string }) =>
            row.version === sample.version && row.kind === sample.kind,
        );
        expect(
          item,
          'Run tests/integration/prepare_packages.py before packaged media checks',
        ).toBeDefined();
        originalPackage = resolve(dirname(probe), item.path);
        packageHash = item.sha256;
        expect(createHash('sha256').update(readFileSync(originalPackage)).digest('hex')).toBe(
          packageHash,
        );
        packageCopy = resolve(dirname(originalPackage), `Browser-${randomUUID()}.${sample.kind}`);
        copyFileSync(originalPackage, packageCopy);
        source = '/projects/' + relative(dirname(probe), packageCopy).split(sep).join('/');
      }
      let game;
      if (sample.kind === 'directory') {
        const imported = await request.post('/api/v2/projects', {
          headers,
          data: { path: source },
        });
        expect(imported.status()).toBe(201);
        game = await imported
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      } else {
        // Exercise the real library page for packaged imports, without intercepting its APIs.
        await page.goto('/library/');
        await page.getByRole('button', { name: '管理菜单' }).click();
        await page.locator('#add-game').click();
        await page.getByRole('radio', { name: '服务器文件', exact: true }).locator('..').click();
        await page.locator('#source-roots').click();
        await page.getByRole('option').first().click();
        for (const folder of source
          .replace(/^\/projects\//, '')
          .split('/')
          .slice(0, -1)) {
          await page.getByRole('button', { name: `${folder}`, exact: true }).click();
        }
        const pending = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === '/api/v2/imports' &&
            response.request().method() === 'POST',
        );
        await page.getByRole('button', { name: `${source.split('/').pop()}`, exact: true }).click();
        await expect(page.locator('#delete-source')).toHaveJSProperty('checked', false);
        await page.getByRole('button', { name: '导入', exact: true }).click();
        const imported = await pending;
        expect(imported.status()).toBe(202);
        const task = await imported
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
        await expect
          .poll(
            async () =>
              (
                await (
                  await request.get(`/api/v2/tasks/imports/${task.task_id}`, { headers })
                )
                  .json()
                  .then((body) =>
                    body && typeof body === 'object' && 'data' in body ? body.data : body,
                  )
              ).state,
          )
          .toBe('completed');
        game = (
          await (
            await request.get(`/api/v2/tasks/imports/${task.task_id}`, { headers })
          )
            .json()
            .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body))
        ).project;
      }
      expect(game.engine_version).toBe(sample.version);
      expect(game.runtime_override).toBeNull();
      expect(game.path.startsWith('/projects/')).toBe(true);
      const project = game.path.startsWith('/projects/')
        ? resolve(dirname(probe), game.path.slice('/projects/'.length))
        : resolve(
            process.env.VN_STREAM_CONTAINER_DATA ?? resolve(probe, '../../data'),
            game.path.slice('/data/'.length),
          );
      const events = () => readEvents(project);
      const baseline = events().length;
      if (packageCopy) {
        expect(readdirSync(resolve(project, 'game')).some((name) => name.endsWith('.rpy'))).toBe(
          false,
        );
        expect(existsSync(resolve(project, '.vn-stream/source'))).toBe(false);
        expect(existsSync(packageCopy)).toBe(true);
        expect(createHash('sha256').update(readFileSync(packageCopy)).digest('hex')).toBe(
          packageHash,
        );
      }
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(String(error)));
      context.on('page', (child) => child.on('pageerror', (error) => errors.push(String(error))));
      // Observe the real decoder output without redirecting or replacing its audio connection.
      await context.addInitScript(() => {
        const target = window as Window & { audioProbes: AnalyserNode[] };
        target.audioProbes = [];
        const original = AudioNode.prototype.connect;
        const observed = new WeakSet<AudioNode>();
        AudioNode.prototype.connect = function (destination: AudioNode, ...args: number[]) {
          const result = original.call(this, destination, ...args);
          if (destination === this.context.destination && !observed.has(this)) {
            observed.add(this);
            const analyser = this.context.createAnalyser();
            analyser.fftSize = 2048;
            original.call(this, analyser);
            target.audioProbes.push(analyser);
          }
          return result;
        };
      });
      let session;
      startingGame = game.project_id;
      if (sample.kind === 'directory') {
        const started = await request.post(`/api/v2/projects/${game.project_id}/start`, {
          headers,
          data: {},
        });
        expect(started.ok()).toBe(true);
        session = await started
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      } else {
        const card = page.locator('main > #details');
        await expect(card.locator('.vn-select-value')).toHaveText(
          `${game.recommendation.build_version}（推荐）`,
        );
        const pending = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === `/api/v2/projects/${game.project_id}/start`,
        );
        await card.getByRole('button', { name: '开始', exact: true }).click();
        const started = await pending;
        expect(started.ok()).toBe(true);
        expect(started.request().postDataJSON().runtime_id).toBeNull();
        // Clicking Start navigates immediately. Chromium may discard the old
        // frame's response body before CDP retrieves it; read the authoritative
        // session via the authenticated API and also check the player's URL.
        session = await (
          await request.get('/api/v2/session', { headers })
        )
          .json()
          .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
        await expect
          .poll(() => new URL(page.url()).searchParams.get('session'))
          .toBe(session.session_id);
      }
      await expect
        .poll(() =>
          events()
            .slice(baseline)
            .some((row) => row.event === 'main_menu'),
        )
        .toBe(true);
      if (sample.kind === 'directory') {
        await page.goto('/');
      }
      await page.waitForFunction(
        () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
      );
      const video = page.locator('#videoStream');
      const box = (await video.boundingBox())!;
      // Selkies overlays a transparent input element over the video; send actual pointer events.
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      await page.keyboard.press('Enter');
      await expect
        .poll(() =>
          events()
            .slice(baseline)
            .some((row) => row.event === 'ready'),
        )
        .toBe(true);
      expect(
        events()
          .slice(baseline)
          .find((row) => row.event === 'ready')?.version,
      ).toMatch(new RegExp('^' + sample.version.replaceAll('.', '\\.') + '\\.'));
      const beforeSave = saveRevision(project);
      await page.keyboard.press('s');
      await expect.poll(() => saveRevision(project)).not.toBe(beforeSave);
      expect(saveRevision(project)).not.toBe('');
      await expect
        .poll(async () =>
          page.evaluate(() => {
            const probes = (window as Window & { audioProbes: AnalyserNode[] }).audioProbes;
            return probes.some((analyser) => {
              const samples = new Float32Array(analyser.fftSize);
              analyser.getFloatTimeDomainData(samples);
              const frequencies = new Float32Array(analyser.frequencyBinCount);
              analyser.getFloatFrequencyData(frequencies);
              let bin = 0;
              for (let i = 1; i < frequencies.length; i++)
                if (frequencies[i] > frequencies[bin]) bin = i;
              const hz = (bin * analyser.context.sampleRate) / analyser.fftSize;
              return (
                analyser.context.state === 'running' &&
                Math.max(...samples.map(Math.abs)) > 0.01 &&
                hz > 410 &&
                hz < 470
              );
            });
          }),
        )
        .toBe(true);
      // The authored idle scene has no primary-color bands: a negative control.
      expect(Math.min(...primaryFractions(await frameSample(page)))).toBeLessThan(0.01);
      await page.keyboard.press('v');
      await page.waitForTimeout(1200);
      const firstFrame = await frameSample(page);
      const first = await video.screenshot();
      await page.waitForTimeout(600);
      const secondFrame = await frameSample(page);
      const second = await video.screenshot();
      const colors = primaryFractions(secondFrame);
      expect(Math.min(...colors)).toBeGreaterThan(0.01);
      let changed = 0;
      for (let i = 0; i < firstFrame.length; i += 3) {
        if (
          [0, 1, 2].some(
            (channel) => Math.abs(firstFrame[i + channel] - secondFrame[i + channel]) > 80,
          )
        )
          changed++;
      }
      const motion = changed / (firstFrame.length / 3);
      // Large color changes across many samples exclude a cursor or minor paint-over noise.
      expect(motion).toBeGreaterThan(0.005);
      await testInfo.attach('movie-features', {
        body: JSON.stringify({ colors, motion }),
        contentType: 'application/json',
      });
      expect(createHash('sha256').update(first).digest('hex')).not.toBe(
        createHash('sha256').update(second).digest('hex'),
      );
      await testInfo.attach('streamed-movie', { body: second, contentType: 'image/png' });
      await page.keyboard.press('Escape');
      await page.close();
      const stillRunning = await (
        await request.get('/api/v2/session', { headers })
      )
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      expect(stillRunning.pid).toBe(session.pid);
      expect(stillRunning.state).toBe('running');
      const replacement = await context.newPage();
      await replacement.goto('/');
      await replacement.waitForFunction(
        () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
      );
      const afterDisconnect = events().length;
      const replacementBox = (await replacement.locator('#videoStream').boundingBox())!;
      await replacement.mouse.click(
        replacementBox.x + replacementBox.width / 2,
        replacementBox.y + replacementBox.height / 2,
      );
      await replacement.keyboard.press('l');
      await expect
        .poll(
          () =>
            events()
              .slice(afterDisconnect)
              .find((row) => row.event === 'loaded')?.counter,
        )
        .toBe(42);
      expect(
        events()
          .slice(afterDisconnect)
          .find((row) => row.event === 'loaded')?.marker,
      ).toBe('persisted');
      expect(errors).toEqual([]);
    } finally {
      try {
        if (startingGame) {
          const current = await (
            await request.get('/api/v2/session', { headers })
          )
            .json()
            .then((body) =>
              body && typeof body === 'object' && 'data' in body ? body.data : body,
            );
          if (current.state === 'running' && current.project_id === startingGame) {
            await request.post('/api/v2/session/stop', { headers, data: {} });
          }
        }
      } finally {
        if (packageCopy && originalPackage) {
          try {
            expect(createHash('sha256').update(readFileSync(packageCopy)).digest('hex')).toBe(
              packageHash,
            );
            expect(createHash('sha256').update(readFileSync(originalPackage)).digest('hex')).toBe(
              packageHash,
            );
          } finally {
            unlinkSync(packageCopy);
          }
        }
      }
    }
  });
}

test('touch reaches game buttons in portrait and landscape Chromium emulation', async ({
  browser,
  baseURL,
  request,
}) => {
  const mobile = await browser.newContext({
    baseURL,
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 844 },
  });
  const imported = await (
    await request.post('/api/v2/projects', { headers, data: { path: '/projects/Stream-Probe' } })
  )
    .json()
    .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
  const before = readEvents().length;
  await request.post(`/api/v2/projects/${imported.project_id}/start`, { headers, data: {} });
  try {
    await expect
      .poll(() =>
        readEvents()
          .slice(before)
          .some((row) => row.event === 'main_menu'),
      )
      .toBe(true);
    const page = await mobile.newPage();
    await signIn(page);
    await page.goto('/');
    await page.waitForFunction(
      () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
    );
    const initialBox = (await page.locator('#videoStream').boundingBox())!;
    await page.touchscreen.tap(
      initialBox.x + initialBox.width * 0.13,
      initialBox.y + initialBox.height * 0.34,
    );
    await expect
      .poll(() =>
        readEvents()
          .slice(before)
          .some((row) => row.event === 'ready'),
      )
      .toBe(true);
    const beforeSave = saveRevision();
    await page.touchscreen.tap(
      initialBox.x + initialBox.width * 0.13,
      initialBox.y + initialBox.height * 0.46,
    );
    await expect.poll(saveRevision).not.toBe(beforeSave);
    expect(saveRevision()).not.toBe('');
    for (const viewport of [
      { width: 390, height: 844 },
      { width: 844, height: 390 },
    ]) {
      await page.setViewportSize(viewport);
      const box = (await page.locator('#videoStream').boundingBox())!;
      const loadedBefore = readEvents().filter((row) => row.event === 'loaded').length;
      // Fixed logical button coordinates belong only to this authored fixture.
      const y = 0.62;
      await page.touchscreen.tap(box.x + box.width * 0.13, box.y + box.height * y);
      await expect
        .poll(() => readEvents().filter((row) => row.event === 'loaded').length)
        .toBeGreaterThan(loadedBefore);
    }
  } finally {
    await request.post('/api/v2/session/stop', { headers, data: {} });
    await mobile.close();
  }
});
