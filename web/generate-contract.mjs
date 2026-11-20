import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = fileURLToPath(new URL('.', import.meta.url));
const repo = fileURLToPath(new URL('..', import.meta.url));
const output = new URL('./api/openapi-v2.json', import.meta.url);
const schema = execFileSync(process.env.VN_STREAM_CONTRACT_PYTHON ?? './.venv/bin/python', [
  'scripts/export-openapi.py',
], { cwd: repo, env: { ...process.env, PYTHONPATH: 'src' } });

const checking = process.argv.includes('--check');
if (checking && !schema.equals(readFileSync(output))) {
  throw new Error('OpenAPI contract is stale. Run npm run contract:generate.');
}
if (!checking) writeFileSync(output, schema);
const scratch = checking ? mkdtempSync(join(tmpdir(), 'vn-types-')) : null;
try {
  execFileSync('./node_modules/.bin/openapi-ts', scratch ? ['-o', scratch] : [], {
    cwd: web,
    stdio: 'inherit',
  });
  if (scratch && !readFileSync(join(scratch, 'types.gen.ts')).equals(
    readFileSync(new URL('./src/api/generated/types.gen.ts', import.meta.url)),
  )) {
    throw new Error('Generated API types are stale. Run npm run contract:generate.');
  }
} finally {
  if (scratch) rmSync(scratch, { force: true, recursive: true });
}
