import { chromium, defineConfig } from '@playwright/test';

if (!process.env.VN_STREAM_CONTAINER_URL) {
  throw new Error('Set VN_STREAM_CONTAINER_URL for an isolated running verification container');
}
export default defineConfig({
  testDir: './tests-container',
  outputDir: '../var/container-verify/browser-results',
  reporter: [['list'], ['./tests-container/summary-reporter.ts']],
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.VN_STREAM_CONTAINER_URL,
    viewport: { width: 1280, height: 800 },
    launchOptions: { executablePath: chromium.executablePath() },
  },
});
