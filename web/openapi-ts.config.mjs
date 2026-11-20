import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: './api/openapi-v2.json',
  output: { path: 'src/api/generated', indexFile: false },
  plugins: ['@hey-api/typescript'],
});
