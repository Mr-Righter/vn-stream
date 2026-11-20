import path from 'node:path';
import ts from 'typescript';

const root = path.resolve(import.meta.dirname, 'src');
const configPath = path.resolve(import.meta.dirname, 'tsconfig.json');
const config = ts.readConfigFile(configPath, ts.sys.readFile);
const { fileNames, options } = ts.parseJsonConfigFileContent(
  config.config,
  ts.sys,
  import.meta.dirname,
);
const program = ts.createProgram(fileNames, options);
const modules = new Map();
const violations = [];
const navigation = new Set(['app/content-route.ts', 'app/content-navigation.ts']);
const relative = (file) => path.relative(root, file).split(path.sep).join('/');

for (const source of program.getSourceFiles()) {
  const file = relative(source.fileName);
  if (file.startsWith('../') || file.startsWith('api/generated/')) continue;
  const dependencies = [];
  modules.set(file, dependencies);
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) && !ts.isExportDeclaration(statement)) continue;
    const specifier = statement.moduleSpecifier;
    if (!specifier || !ts.isStringLiteral(specifier) || !specifier.text.startsWith('.')) continue;
    const resolved = ts.resolveModuleName(
      specifier.text,
      source.fileName,
      options,
      ts.sys,
    ).resolvedModule;
    if (!resolved) continue; // TypeScript reports unresolved imports.
    const dependency = relative(resolved.resolvedFileName);
    dependencies.push(dependency);

    const layer = file.split('/')[0];
    const target = dependency.split('/')[0];
    const isConsumer = layer === 'features' || layer === 'api' || layer === 'ui';
    if (
      (isConsumer && target === 'app' && !navigation.has(dependency)) ||
      (['ui', 'api'].includes(layer) && target === 'features') ||
      (layer === 'api' && navigation.has(dependency))
    ) {
      violations.push(`${file} → ${dependency}`);
    }
  }
}

const active = new Set();
const visited = new Set();
function visit(file) {
  if (active.has(file)) {
    violations.push(`Circular dependency: ${[...active, file].join(' → ')}`);
    return;
  }
  if (!modules.has(file) || visited.has(file)) return;
  active.add(file);
  for (const dependency of modules.get(file)) visit(dependency);
  active.delete(file);
  visited.add(file);
}

for (const file of modules.keys()) visit(file);
if (violations.length) {
  process.exitCode = 1;
  console.error(violations.join('\n'));
}
