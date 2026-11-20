import { chromium, defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:18081',
    launchOptions: { executablePath: chromium.executablePath() },
  },
  webServer: {
    command:
      'PYTHONPATH=../src ../.venv/bin/python -m vn_stream --data ../var/e2e/data --root ../var/e2e/inbox --web dist --stream-path / --port 18081',
    url: 'http://127.0.0.1:18081/login',
    reuseExistingServer: false,
    env: { VN_STREAM_PASSWORD: 'browser-test-only' },
  },
});
