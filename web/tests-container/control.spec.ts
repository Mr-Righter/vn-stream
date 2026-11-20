import { test, expect } from './fixtures.js';
import { signIn } from './fixtures.js';

test('a new viewing device takes over the single control connection', async ({
  page,
  browser,
  baseURL,
}) => {
  let firstClosed = false;
  page.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname === '/api/websockets') {
      socket.on('close', () => {
        firstClosed = true;
      });
    }
  });
  await page.route('**/api/v2/session', (r) =>
    r.fulfill({ json: { state: 'running', session_id: 'transport-test' } }),
  );
  await page.goto('/');
  await page.waitForFunction(
    () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
  );
  const second = await browser.newContext({
    baseURL,
    viewport: { width: 1280, height: 800 },
  });
  try {
    const replacement = await second.newPage();
    await signIn(replacement);
    await replacement.route('**/api/v2/session', (r) =>
      r.fulfill({ json: { state: 'running', session_id: 'transport-test' } }),
    );
    await replacement.goto('/');
    await replacement.waitForFunction(
      () => (document.querySelector('#videoStream') as HTMLVideoElement)?.videoWidth > 0,
    );
    await expect.poll(() => firstClosed, { timeout: 8000 }).toBe(true);
  } finally {
    await second.close();
  }
});
