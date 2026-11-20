import type { Project, ProjectRuntime } from '../src/api/types.js';

export const engineFixtures = [
  { engine_id: 'renpy', display_name: "Ren'Py", engine_name_key: null },
  { engine_id: 'ons', display_name: 'ONScripter', engine_name_key: 'engine.name.ons' },
  { engine_id: 'krkr', display_name: 'Kirikiri', engine_name_key: 'engine.name.krkr' },
];

export function runtimeFixture(version: string, id = `renpy:${version}`): ProjectRuntime {
  return {
    runtime_id: id,
    display_name: version,
    build_version: version,
    available: true,
    reason: null,
  };
}

export function renpyProjectFixture(overrides: Partial<Project> = {}): Project {
  const recommendation = overrides.recommendation ?? {
    runtime_id: 'renpy:8.3.7',
    build_version: '8.3.7',
    display_name: null,
    kind: 'exact',
  };
  const runtimeOptions =
    recommendation.runtime_id && recommendation.build_version
      ? [runtimeFixture(recommendation.build_version, recommendation.runtime_id)]
      : [];
  return {
    project_id: 'fixture',
    name: 'Fixture',
    path: '/fixture',
    engine_id: 'renpy',
    engine_display_name: "Ren'Py",
    engine_name_key: null,
    engine_version: '8.3.7',
    runtime_id: null,
    runtime_build_version: null,
    runtime_override: null,
    capabilities: {
      assets: true,
      save_management: true,
      save_editing: true,
      save_migration: true,
      native_actions: true,
      exit_mode: 'native',
      uses_original_engine_version: true,
    },
    recognition: { status: 'detected', evidence: [], warnings: [] },
    recommendation,
    runtime_options: runtimeOptions,
    release_version: null,
    icon_url: null,
    size_bytes: null,
    last_played_at: null,
    play_seconds: 0,
    available: true,
    deletion: null,
    ...overrides,
  };
}

/** Wire fixtures use the same envelope shape as the public API. */
export function apiData(body: any): any {
  return body && typeof body === 'object' && 'data' in body ? body.data : body;
}
export function mockEnvelope(url: string, data: any, status = 200): unknown {
  if (!new URL(url).pathname.startsWith('/api/v2/')) return data;
  if (status >= 400)
    return {
      error: data?.error?.code
        ? data.error
        : (data?.messages?.detail ?? { code: 'common.requestFailed', params: {} }),
      request_id: 'fixture-request',
    };
  if (
    data &&
    typeof data === 'object' &&
    'total' in data &&
    ('page' in data || 'entries' in data)
  ) {
    const { total, page = Number(new URL(url).searchParams.get('page') ?? 1), ...records } = data;
    return { data: records, pagination: { total, page, page_size: 100 } };
  }
  return { data };
}
