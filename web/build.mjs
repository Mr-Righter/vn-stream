import { cp, mkdir, rm, readFile, writeFile } from 'node:fs/promises';
import { build } from 'esbuild';
const visualTheme = JSON.parse(await readFile('../src/vn_stream/ui/theme.json', 'utf8'));
import { Moon, Sun } from 'lucide';
await rm('dist', { recursive: true, force: true });
await mkdir('dist', { recursive: true });
await cp('public', 'dist', { recursive: true });
const cssVariables = (appearance) => {
    const tokens = { ...visualTheme.token, ...visualTheme.modes[appearance] };
    return (
        Object.entries(visualTheme.cssTokens)
            .map(([name, key]) => {
                const value = tokens[key];
                return `--vn-${name}:${name.startsWith('motion-') ? `${Math.round(parseFloat(value) * 1000)}ms` : typeof value === 'number' ? value + (key.startsWith('lineHeight') || key.startsWith('fontWeight') ? '' : 'px') : value}`;
            })
            .join(';') +
        `;--vn-font-compact:${visualTheme.compactFontSize}px;--vn-weight-body:${visualTheme.fontWeightRegular};--vn-weight-medium:${visualTheme.fontWeightMedium}`
    );
};
await writeFile(
    'dist/theme.css',
    `:root{${cssVariables('dark')};color-scheme:dark}:root[data-theme="light"]{${cssVariables('light')};color-scheme:light}
@media(prefers-reduced-motion:reduce){:root,:root[data-theme="light"]{--vn-motion-fast:0ms;--vn-motion-mid:0ms;--vn-motion-slow:0ms}}`,
);
const fontBuild = await build({
    entryPoints: ['node_modules/@fontsource-variable/noto-sans-sc/index.css'],
    bundle: true,
    minify: true,
    outdir: 'dist',
    entryNames: 'fonts/font-[hash]',
    assetNames: 'fonts/[hash]',
    loader: { '.woff2': 'file' },
    metafile: true,
});
const fontStyles = Object.keys(fontBuild.metafile.outputs)
    .find((path) => path.endsWith('.css'))
    .replace(/^dist\//, '');
await cp('node_modules/@fontsource-variable/noto-sans-sc/LICENSE', 'dist/fonts/OFL-1.1.txt');
const bootstrap = `try { const appearance = JSON.parse(localStorage.getItem('vn-stream:preferences') || '{}').appearance === 'light' ? 'light' : 'dark'; document.documentElement.dataset.theme = appearance; document.querySelector('meta[name="theme-color"]')?.setAttribute('content', ${JSON.stringify(visualTheme.modes)}[appearance].colorBgLayout); } catch {}`;

await mkdir('dist/icons', { recursive: true });
for (const [name, nodes] of [
    ['moon', Moon],
    ['sun', Sun],
]) {
    const content = nodes
        .map(
            ([tag, attrs]) =>
                `<${tag} ${Object.entries(attrs)
                    .filter(([key]) => key !== 'key')
                    .map(([key, value]) => `${key}="${value}"`)
                    .join(' ')} />`,
        )
        .join('');
    await writeFile(
        `dist/icons/${name}.svg`,
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${content}</svg>`,
    );
}
const english = JSON.parse(await readFile('../src/vn_stream/i18n/locales/en.json', 'utf8'));
const defaults = JSON.parse(await readFile('../src/vn_stream/i18n/locales/zh-CN.json', 'utf8'));
const loadFailure = `  let english = false;
  try { english = JSON.parse(localStorage.getItem('vn-stream:preferences') || '{}').language === 'en'; } catch {}
  const alert = document.createElement('p');
  alert.setAttribute('role', 'alert');
  alert.textContent = english ? ${JSON.stringify(english['ui.assetLoadFailed'])} : ${JSON.stringify(defaults['ui.assetLoadFailed'])};
  let appearance = 'dark';
  try { appearance = JSON.parse(localStorage.getItem('vn-stream:preferences') || '{}').appearance === 'light' ? 'light' : 'dark'; } catch {}
  const colors = ${JSON.stringify(visualTheme.modes)}[appearance];
  Object.assign(alert.style, { position:'fixed', top:'16px', left:'16px', right:'16px', zIndex:'2147483647', padding:'16px', borderRadius:${JSON.stringify(visualTheme.token.borderRadius + 'px')}, color:colors.colorErrorText, background:colors.colorBgElevated, border:'1px solid ' + colors.colorErrorText, font:${JSON.stringify(visualTheme.token.fontSize + 'px/' + visualTheme.token.lineHeight + ' ' + visualTheme.token.fontFamily)} });
  document.querySelector('#login-error')?.removeAttribute('role');
  document.body.append(alert);`;
for (const file of ['index.html', 'login.html']) {
    const source = (await readFile('dist/' + file, 'utf8'))
        .replace(
            'content="__VN_THEME_BACKGROUND__"',
            `content="${visualTheme.modes.dark.colorBgLayout}"`,
        )
        .replace(
            '</head>',
            `<script>${bootstrap}</script><link rel="stylesheet" href="./theme.css"><link rel="stylesheet" href="./${fontStyles}"></head>`,
        );
    const output = source.replace(
        /(data-i18n="([^"]+)"\s*>)[^<]*/g,
        (_, open, key) =>
            open +
            (defaults[key] ?? '')
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;'),
    );
    const errorHandler = loadFailure
        .replaceAll('&', '&amp;')
        .replaceAll('"', '&quot;')
        .replaceAll('<', '&lt;');
    await writeFile(
        'dist/' + file,
        output.replace('<script type="module"', `<script onerror="${errorHandler}" type="module"`),
    );
}
const result = await build({
    entryPoints: {
        main: 'src/main.tsx',
        'game-controls': 'src/game-controls.tsx',
        login: 'src/login.tsx',
    },
    bundle: true,
    splitting: true,
    format: 'esm',
    target: 'es2022',
    outdir: 'dist',
    entryNames: 'chunks/[name]-[hash]',
    metafile: true,
    chunkNames: 'chunks/[name]-[hash]',
    minify: true,
});

// Stable server entry names delegate to hashed application bundles. Catching
// import failures here also covers a missing shared chunk during an upgrade.
for (const [output, metadata] of Object.entries(result.metafile.outputs)) {
    if (!metadata.entryPoint) continue;
    const name = metadata.entryPoint
        .split('/')
        .pop()
        .replace(/\.tsx?$/, '');
    const target = './' + output.replace(/^dist\//, '');
    await writeFile(
        `dist/${name}.js`,
        `for (const path of ['./theme.css', ${JSON.stringify('./' + fontStyles)}]) {
  const href = new URL(path, import.meta.url).href;
  if (![...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.href === href)) {
    const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = href; document.head.append(link);
  }
}
const entry = new URL(${JSON.stringify(target)}, import.meta.url);
entry.search = new URL(import.meta.url).search;
import(entry.href).catch(() => {
${loadFailure}
});
`,
    );
}

// Follow the login import graph instead of exposing a whole static directory.
const loginEntry = Object.entries(result.metafile.outputs).find(
    ([, data]) => data.entryPoint === 'src/login.tsx',
)[0];
const loginAssets = new Set(['/theme.css', '/ui.css', '/icons/moon.svg', '/icons/sun.svg']);
function includeAsset(path) {
    if (loginAssets.has('/' + path.replace(/^dist\//, ''))) return;
    loginAssets.add('/' + path.replace(/^dist\//, ''));
    for (const imported of result.metafile.outputs[path]?.imports ?? [])
        if (!imported.external) includeAsset(imported.path);
}
includeAsset(loginEntry);
for (const path of Object.keys(fontBuild.metafile.outputs))
    loginAssets.add('/' + path.replace(/^dist\//, ''));
await writeFile('dist/public-assets.json', JSON.stringify([...loginAssets].sort(), null, 2));
