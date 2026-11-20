import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));

// Credentials stay in the local ignored file; do not attach HTTP traces to these tests.
export const settings = Object.fromEntries(
  readFileSync(process.env.VN_STREAM_CONTAINER_ENV ?? resolve(root, 'containers/.env'), 'utf8')
    .split('\n')
    .filter((line) => line.includes('=') && !line.startsWith('#'))
    .map((line) => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]),
);
export const credentials = {
  username: settings.VN_STREAM_USERNAME ?? 'player',
  password: settings.VN_STREAM_PASSWORD,
};
export const headers: Record<string, string> = {};
export const probe =
  process.env.VN_STREAM_CONTAINER_PROBE ?? resolve(root, 'var/container-verify/inbox/Stream-Probe');

export function events(project = probe): Record<string, unknown>[] {
  try {
    return readFileSync(resolve(project, 'events.jsonl'), 'utf8')
      .split('\n')
      .filter((line) => line.endsWith('}'))
      .map((line) => JSON.parse(line));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
}
