import { test, expect } from './fixtures.js';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { events } from './support.js';

// Only newly generated self-authored fixtures in the isolated verification mount.
// Explicit test-only renderer control; never changes service/container defaults.
const software = process.env.VN_STREAM_TEST_SOFTWARE === '1';
const versions = ['7.5.3', '7.8.2', '8.0.3', '8.1.3', '8.2.3', '8.3.2', '8.3.7', '8.4.1', '8.5.3'];
const cases = versions.map((version) => ({ version, width: 801, height: 451, delay: 0 }));
cases.push(
  ...[
    { version: '8.5.3', width: 1024, height: 768, delay: 0 },
    { version: '8.5.3', width: 1280, height: 720, delay: 0 },
    { version: '8.5.3', width: 2560, height: 1440, delay: 0 },
    { version: '8.5.3', width: 5120, height: 2880, delay: 0 },
    { version: '8.5.3', width: 3000, height: 4000, delay: 0 },
    { version: '8.5.3', width: 1920, height: 1080, delay: 7 },
    { version: '8.5.3', width: 1024, height: 768, delay: 7 },
  ],
);
for (const { version, width, height, delay } of cases) {
  test(`native ${version} ${width}x${height} initialization delay ${delay}s: render, capture, browser clicks`, async ({
    page,
    request,
  }) => {
    expect(process.env.VN_STREAM_CONTAINER_URL).toMatch(
      /^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/,
    );
    const state = await (
      await request.get('/api/v2/session')
    )
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    expect(['stopped', 'failed']).toContain(state.state);
    const name = `Native-${version}-${randomUUID().slice(0, 8)}`;
    const project = resolve('../var/container-verify/inbox', name);
    await mkdir(project + '/game', { recursive: true });
    const script = `define config.name = "Native Canvas Probe"
define config.screen_width = 640
define config.screen_height = 360
init python:
    import os
    import json
${software ? '    os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"\n' : ''}    gui.init(640, 360)
    def native_event(event):
        with open(os.path.join(config.basedir, "events.jsonl"), "a") as f:
            f.write(json.dumps({"event": event, "physical": list(renpy.get_physical_size()), "version": renpy.version_only, "vsync": os.environ.get("RENPY_GL_VSYNC")}) + "\\n")
init 1000 python:
    import time
    time.sleep(${delay})
    config.screen_width = ${width}
    config.screen_height = ${height}
screen main_menu():
    tag menu
    add Solid("#14213d")
    text "Native canvas" xpos 20 ypos 20 color "#ffffff"
    button:
        xpos int(config.screen_width * .65)
        ypos int(config.screen_height * .55)
        xsize int(config.screen_width * .2)
        ysize int(config.screen_height * .2)
        background Solid("#eb5e28")
        action Function(native_event, "clicked")
    on "show" action Function(native_event, "menu")
label start:
    return
`;
    await writeFile(project + '/game/script.rpy', script);
    const imported = await request.post('/api/v2/projects', {
      data: { path: '/projects/' + name },
    });
    expect(imported.ok()).toBe(true);
    const game = await imported
      .json()
      .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
    let session: { session_id: string; pid: number; display: { state: string } } | undefined;
    try {
      const started = await request.post(`/api/v2/projects/${game.project_id}/start`, {
        data: { runtime_id: `renpy:${version}` },
      });
      expect(started.ok()).toBe(true);
      session = await started
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      if (delay) expect(session!.display.state).toBe('fallback');
      if (delay && width === 1024) {
        // Keep the real 1080p capture connected while the first browser API poll
        // arrives only after the native upgrade, exercising the revision-2 race.
        let firstPoll = true;
        await page.route('**/api/v2/session', async (route) => {
          if (firstPoll) {
            firstPoll = false;
            await new Promise((resolve) => setTimeout(resolve, 4000));
          }
          await route.continue();
        });
      }
      await page.goto('/?session=' + session!.session_id);
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
            ).display?.state,
          { timeout: 20000 },
        )
        .toBe('native');
      const scale = Math.min(1, 3840 / Math.max(width, height), 2160 / Math.min(width, height));
      const content = [Math.floor(width * scale), Math.floor(height * scale)];
      const capture = [Math.ceil(content[0] / 8) * 8, Math.ceil(content[1] / 2) * 2];
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
            ).display?.render?.physical,
          { timeout: 20000 },
        )
        .toEqual(
          content.some((n, i) => n !== capture[i]) && !['7.5.3', '8.0.3'].includes(version)
            ? content
            : capture,
        );
      await expect
        .poll(() => events(project).filter((e) => e.event === 'menu').length, { timeout: 20000 })
        .toBeGreaterThan(0);
      await page.waitForFunction(
        ([w, h]) => {
          const video = document.querySelector('#videoStream') as HTMLVideoElement;
          const canvases = Array.from(
            document.querySelectorAll<HTMLCanvasElement>('#videoCanvas, #videoWorkerCanvas'),
          );
          return (
            (video?.readyState >= 2 && video.videoWidth === w && video.videoHeight === h) ||
            canvases.some(
              (canvas) =>
                getComputedStyle(canvas).display !== 'none' &&
                canvas.width === w &&
                canvas.height === h,
            )
          );
        },
        capture,
        { timeout: 20000 },
      );
      if (version === '8.1.3') {
        // Inspect the received video itself: external alignment padding is black,
        // with no title bar/window decoration in the capture.
        const edgePixels = await page.evaluate(() => {
          const video = document.querySelector<HTMLVideoElement>('#videoStream')!;
          const frame = document.createElement('canvas');
          frame.width = video.videoWidth;
          frame.height = video.videoHeight;
          const ctx = frame.getContext('2d')!;
          ctx.drawImage(video, 0, 0);
          return [
            [0, Math.floor(frame.height / 2)],
            [frame.width - 1, Math.floor(frame.height / 2)],
            [Math.floor(frame.width / 2), frame.height - 1],
          ].map(([x, y]) => Array.from(ctx.getImageData(x, y, 1, 1).data).slice(0, 3));
        });
        expect(edgePixels.every((pixel) => pixel.every((channel) => channel < 32))).toBe(true);
        console.log(JSON.stringify({ version, receivedFrameBlackEdges: edgePixels }));
      }
      expect(events(project).find((e) => e.event === 'menu')?.vsync).toBe('0');
      const current = await (
        await request.get('/api/v2/session')
      )
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      expect(current.session_id).toBe(session!.session_id);
      expect(current.pid).toBe(session!.pid);
      expect(current.display.source).toEqual([width, height]);
      expect(current.display.content).toEqual(content);
      expect(current.display.capture).toEqual(capture);
      expect(current.display.render.viewport.slice(2)).toEqual(content);
      if (delay) expect(current.display.revision).toBe(2);
      for (const viewport of [
        { width: 1280, height: 800 },
        { width: 390, height: 844 },
        { width: 844, height: 390 },
      ]) {
        await page.setViewportSize(viewport);
        await page.waitForTimeout(300);
        const input = await page.locator('#overlayInput').boundingBox();
        console.log(
          JSON.stringify({
            viewport,
            input,
            video: await page.evaluate(() =>
              document.querySelector('#videoStream')?.getBoundingClientRect().toJSON(),
            ),
            canvas: await page.evaluate(() =>
              document.querySelector('#videoCanvas')?.getBoundingClientRect().toJSON(),
            ),
          }),
        );
        await page.screenshot({
          path: resolve('../var/antd-work', `native-${version}-${viewport.width}.png`),
        });
        expect(input).not.toBeNull();
        let [px, py] = current.display.render.viewport;
        px += current.display.render.position?.[0] ?? 0;
        py += current.display.render.position?.[1] ?? 0;
        const x = input!.x + ((px + content[0] * 0.75) / capture[0]) * input!.width;
        const y = input!.y + ((py + content[1] * 0.65) / capture[1]) * input!.height;
        const count = events(project).filter((e) => e.event === 'clicked').length;
        await page.mouse.click(x, y);
        await expect
          .poll(() => events(project).filter((e) => e.event === 'clicked').length)
          .toBeGreaterThan(count);
        expect(
          (
            await (
              await request.get('/api/v2/session')
            )
              .json()
              .then((body) =>
                body && typeof body === 'object' && 'data' in body ? body.data : body,
              )
          ).display.capture,
        ).toEqual(capture);
      }
      await writeFile(
        resolve('../var/antd-work', `native-${version}-${width}x${height}-${delay}.json`),
        JSON.stringify(
          {
            version,
            software,
            width,
            height,
            pid: current.pid,
            display: current.display,
            events: events(project),
            renderer: (await readFile(project + '/log.txt', 'utf8'))
              .split('\n')
              .filter((line) => /Renderer:|Vendor:|Screen sizes:/.test(line)),
          },
          null,
          2,
        ),
      );
      expect(await readFile(project + '/game/script.rpy', 'utf8')).toBe(script);
    } finally {
      // Close media before stopping the game or changing the next display mode.
      await page.close();
      const current = await (
        await request.get('/api/v2/session')
      )
        .json()
        .then((body) => (body && typeof body === 'object' && 'data' in body ? body.data : body));
      if (session && current.session_id === session.session_id && current.state === 'running')
        await request.post('/api/v2/session/stop', { data: {} });
    }
  });
}
