# Working on VN Stream

These instructions apply throughout the repository. Keep existing user and concurrent changes intact. [README.md](README.md) covers installing the release image.

## Develop and verify

From the repository root, use Python 3.11 or newer and Node.js 24, the version used by the web image build.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
npm --prefix web ci
npm --prefix web run build
```

Run the local library from the repository root with a disposable password and isolated data. Use Docker for Selkies and game streaming. The default Compose endpoint is local HTTP on port 8443. Direct HTTPS uses a separately mounted certificate and key.

```sh
mkdir -p var/dev/games
VN_STREAM_PASSWORD=dev-only .venv/bin/python -m vn_stream \
  --data var/dev/data --root var/dev/games --web web/dist
```

```sh
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/mypy --follow-imports=silent --check-untyped-defs \
  src/vn_stream/engines/contracts.py src/vn_stream/storage src/vn_stream/projects/maintenance.py
npm --prefix web run check
npm --prefix web run build
(cd web && npx playwright install chromium && npx playwright test)
```

- The API serves `/api/v2`. When API models change, run `npm --prefix web run contract:generate` and `npm --prefix web run contract:check`. Commit the [OpenAPI schema](web/api/openapi-v2.json) and generated TypeScript together.
- [Backend dependency tests](tests/unit/test_module_dependencies.py) enforce import direction and cycle boundaries. Test parsing, path handling and version ordering across the supported input domain. Use self-authored fixtures for destructive tests.
- The default Python suite skips tests that need real engines or container services. For the Ren'Py compatibility suite, configure `VN_STREAM_RUNTIME_SMOKE=1`, `VN_STREAM_RUNTIME_ROOT`, and private Xvfb, xdotool and xauth, then run `.venv/bin/python -m pytest -q tests/compatibility`. Container regressions use `VN_STREAM_CONTAINER_URL` and `VN_STREAM_CONTAINER_ENV` with `npm --prefix web run test:container`. Point them at an isolated instance with self-authored projects.
- Verify actual video and audio, input, reconnect, and save/load for streaming or runtime changes. Check phone certificate trust and the home-screen app on a real phone when the change affects them. Report what ran, what was skipped and why.

## Architecture and conventions

- VN Stream is a modular monolith using Python, FastAPI, SQLite, React, Ant Design, TypeScript, esbuild, and a pinned Selkies image. Start the `vn_stream` package with `python -m vn_stream`. VN Stream settings use `VN_STREAM_*`, and the application lives at `/opt/vn-stream` in the container.
- `api` validates and serializes protocol data and maps errors. `application` wires dependencies, owns their lifecycles, and coordinates use cases across modules. Keep HTTP imports out of domain modules and domain and codec imports out of `storage`.
- The built-in [engine registry](src/vn_stream/engines/registry.py) is the engine list. Each adapter implements `inspect_project`, `resolve_runtime`, and `prepare_launch` and owns runtime requirements and engine-specific hooks. Pass a `LaunchPlan` to the shared session supervisor when adding engine behavior. Reuse the existing routes and supervisor.
- `projects` owns identity, discovery, maintenance and deletion. `imports` owns extraction, publication and recovery checkpoints. `saves` owns save transactions. Cross module boundaries through public interfaces and keep each module's private members and tables inside that module.
- Maintenance checks and changes run under the project lock. Acquire the import operation lock first, followed by the project lock and the database transaction. Keep blocking locks off async wait paths. Send bounded synchronous SQLite, archive and file work to workers. On cancellation, wait for submitted writes before rolling back, closing handles and releasing locks.
- Use the same publication interface for import and restart recovery. Reconcile filesystem checkpoints and database registration together. Protect existing project directories from overwrite. Give every worker, thread and subprocess an owner and keep cleaning up other resources if one close fails.
- Use `snake_case` for Python, `PascalCase` for types and React components, `kebab-case` for TypeScript filenames, and `camelCase` for TypeScript functions. Use `vn-*` CSS classes and `--vn-*` variables. Use `project_id`, `engine_id`, `runtime_id`, `session_id`, and `task_id` consistently, UTC RFC 3339 for timestamps, `*_bytes` and `*_seconds` for units, and `progress_percent` for percentages.
- Write comments and docstrings in English. Use shared English and Simplified Chinese message resources for user-facing text and stable keys for logic. Keep modules focused and `__init__.py` free of thread startup, database initialization, or hidden registration. Load Python locale, theme and Ren'Py hook data as package resources so the wheel and container work from any directory.

## Browser and player

- `web/dist/` is the build output. Fingerprint `main.js`, `login.js`, `game-controls.js`, shared chunks, CSS, fonts and icons. Serve frontend assets locally.
- Keep API transport and types in `web/src/api/`. Feature controllers own uploads, polling and playback and expose stable snapshots to React. Preserve these operations, drafts and forms when preferences change.
- Use the shared `theme.json` and Ant Design `ConfigProvider` for theme values. Maintain focus visibility, reduced motion, safe areas and mobile layouts.
- The React player root owns the toolbar. Preserve the Selkies media, input coordinate and IME nodes and same-tab web app navigation with scope `/`.
- Keep one active session. Retain it for at most 60 seconds after the last control heartbeat from the current media owner. Treat process exit and save success as separate events. Use backend capabilities to gate UI and reject unsupported calls on the server. Keep Ren'Py signing, variable editing and action hooks specific to Ren'Py.

## Data and security

- Container bind mounts provide `/games` and `/data`. Games and runtimes are grouped under `renpy/`, `onscripter/`, and `kirikiri/` inside `/games` and `/data/runtimes`. The internal family IDs are `renpy`, `ons`, and `krkr`. Resolve directory names through the adapter registry.
- Preserve the original game, actual saves, and `persistent` progress. Keep the project's `.vn-stream/` directory with it, including its identity, isolated HOME, and asset index. Runtime switches update the selection without taking a full project snapshot. Keep small save backup, restore, editing, signing and migration workflows available wherever supported.
- Inspect game files statically. Do not execute scripts, deserialize RPYC, or invoke arbitrary object constructors, `pickle.loads`, or `eval` when inspecting games or saves. Restrict external save edits to existing, reliably located simple variables and retain complex objects and rollback state. Back up all confirmed save copies, validate revisions, write atomically, and persist a recovery journal before editing.
- Archive extraction and directory uploads reject escaping paths, symlinks, conflicts, and reserved directories. Preserve the default bounds of 20 GiB, 100,000 files, and 32 levels, with up to 1,000 manifest entries per request and 8 MiB chunks for directory uploads.
- Index original assets in place. Require authenticated media requests and retain HTTP Range support. Keep conversion and thumbnail work bounded and cancellable, and scope caches to the index snapshot. Preserve cookie, CSRF, and same-origin checks. Keep credentials, session tokens, private keys, and raw exceptions out of client responses and Git.
- Keep `RENPY_SEARCHPATH`, `SELKIES_*`, standard save names, and third-party resource names intact. Keep build output, local configuration, virtual environments, databases, games, SDKs, and private fixtures out of source control and the public release.

## Maintenance and release

- The release target is Linux x86-64. Keep base images, SDK versions, native source commits, and build inputs pinned, verify artifacts against their recorded digests, and retain upstream licenses and notices. The [Ren'Py manifest](src/vn_stream/runtimes/releases.py), [native build script](scripts/build-native-runtime.py), and [native candidate lock](containers/native-candidates.lock.json) record source identities. The [Next packaging tool](scripts/package-next-runtime.py) verifies and packages its host, engine library and licenses.
- Verify that a new release image starts with an empty data mount and exposes nine Ren'Py SDKs and six native runtimes. Keep Yuri and krkr-rs as the automatic defaults for their families. Require explicit selection of Jh, zero, NEW, and Next and enforce project restrictions again at launch. Verify bundled runtimes with reproducible builds or artifacts with pinned provenance. Reject overwritten installs and unverified runtime substitutions.
- Export a reviewed commit with `python3 scripts/export-public-tree.py --output var/public-checkout` before publishing from a private checkout. The export includes the two READMEs, `AGENTS.md`, source, test fixtures, and required license notices. Review the fresh tree before importing it into the public repository.
- Attach `native-runtimes-linux-amd64.tar.gz` and `runtime-sources-linux-amd64.tar.gz` to the draft GitHub release. The [bundle tool](scripts/prepare-runtime-bundle.py) checks them against the [native runtime lock](containers/runtime-bundle.lock.json) and fetches the official pinned Ren'Py SDKs. The [release workflow](.github/workflows/publish.yml) verifies and publishes the versioned image. After the package is public and the release is accepted, run the workflow's `promote_version` action. It checks an anonymous pull and assigns `latest` to the tested digest.
- Before deployment, verify the release in a separate container with synthetic projects. Check login, import, streaming, input, reconnect, save/load and persistent data after container replacement. Check the active session, uploads, imports, and maintenance jobs on the target instance. Update only while idle and keep the previous image and deployment configuration for rollback. Stop the running instance before another version writes to the same data or games.
- API v2 reuses existing VN Stream data. When moving from the earlier Ren'Py Stream application, preserve the old data and independently locate the real saves, `persistent` and old HOME progress. For VN Stream engine directory changes, use `.venv/bin/python -m vn_stream.projects.relocation --help` to prepare an offline `preview` and inspect its plan before `apply`. Resume or roll back an interrupted migration with the same plan. Keep the old mount configuration for rollback and preserve newer game progress before restoring any database or backups.
- Review intended changes and run `git diff --check` before committing. Keep commits independently reviewable. Preserve existing installations and other work while testing, deploying or rolling back.
