import { test, expect } from './fixtures.js';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });
test('mobile upload imports an encrypted archive after password retry without retransmission', async ({
  page,
}) => {
  const title = `Mobile Story ${Date.now()}`;
  const payload = execFileSync(fileURLToPath(new URL('../../.venv/bin/python', import.meta.url)), [
    '-c',
    `
import io,json,sys,pyzipper
out=io.BytesIO()
with pyzipper.AESZipFile(out,'w',encryption=pyzipper.WZ_AES) as archive:
 archive.setpassword(b'package-test')
 archive.writestr('Story/game/script.rpy','label start:\\n return')
 archive.writestr('Story/game/cache/build_info.json',json.dumps({'name':sys.argv[1],'version':'0.7'}))
sys.stdout.buffer.write(out.getvalue())
`,
    title,
  ]);
  let chunks = 0;
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/api/v2/uploads/archives/')) chunks++;
  });
  await page.goto('/library/');
  await page.getByRole('button', { name: '管理菜单' }).click();
  await page.locator('#add-game').click();
  await page
    .getByLabel('选择文件', { exact: true })
    .setInputFiles({ name: 'Mobile Story.zip', mimeType: 'application/zip', buffer: payload });
  await page.getByLabel('解压密码（可选）').fill('wrong');
  await page.getByRole('button', { name: '导入', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试导入', exact: true })).toBeVisible();
  const transferred = chunks;
  expect(transferred).toBe(1);
  await page.getByLabel('解压密码（可选）').fill('package-test');
  await page.getByRole('button', { name: '重试导入', exact: true }).click();
  await expect(page.locator('#add-page')).not.toBeVisible();
  await expect(page.locator('#details h2')).toHaveText(title);
  await expect(page.locator('main > #details')).toContainText('0.7');
  expect(chunks).toBe(transferred);
});
