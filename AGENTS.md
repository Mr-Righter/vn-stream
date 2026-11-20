# Working on VN Stream

VN Stream manages a visual novel library and one active game session. The server runs the game with Ren'Py, ONScripter or Kirikiri, and Selkies streams video and audio to the browser and carries input back to the game.

## Where to start

| If you are changing | Read |
| --- | --- |
| Project imports, persistence, recovery or save writes | [Backend architecture](docs/backend-architecture.md) |
| Project detection, a runtime build, launch arguments or session behavior | [Engine integration](docs/engine-integration.md) |
| A browser feature, player control or API v2 field | [Frontend and API](docs/frontend-and-api.md) |
| Local setup, test environments, Docker images or a release | [Development and release](docs/development-and-release.md) |
| End-user setup or the supported runtime list | [English README](README.md) · [Chinese README](README_ZH.md) |

Use the local Python service for library and API changes. Use the development container to verify real video, audio and input against the pinned Selkies build. [Local and container commands](docs/development-and-release.md#local-setup) · [Selkies integration script](scripts/patch-selkies.py)

## From Play to the first frame

For a new session, Play sends `POST /api/v2/projects/{project_id}/start`. `GameLauncher` rechecks the project and runtime, the engine adapter builds a `LaunchPlan`, and the session supervisor starts the worker that launches the game. The worker reports process state and display readiness separately. [Browser request](web/src/api/library.ts) · [Launcher](src/vn_stream/application/game_launcher.py) · [Adapter contract](src/vn_stream/engines/contracts.py) · [Supervisor](src/vn_stream/sessions/supervisor.py)

## Troubleshoot a session

| Symptom | First check |
| --- | --- |
| Game exits after launch | Read the latest output in `<project>/.vn-stream-session.log`. Compare it with `data.reason` and `data.exit_code` from `GET /api/v2/session` before starting another game. [Session worker](src/vn_stream/sessions/worker.py) |
| Process is running, browser is black | Check `data.readiness` and `data.display.diagnostic` in the session response. Trace the [Ren'Py canvas report](src/vn_stream/streaming/native_canvas.py) or the [ONScripter/Kirikiri window probe](src/vn_stream/streaming/window_canvas.py), then the [Selkies display resize](src/vn_stream/streaming/selkies_display.py). |
| Game responds to input, toolbar actions are missing or disabled | Inspect status messages on `/api/v2/session/control`. Compare `supported`, `available` and controller ownership with the browser state. [Control WebSocket](src/vn_stream/api/routes/player.py) · [Player controller](web/src/features/player/player-controller.ts) |

## Focused checks

Run from the repository root after [setting up the development tools](docs/development-and-release.md#local-setup). Substitute the test or source file you changed.

| Task | Command |
| --- | --- |
| Backend behavior | `.venv/bin/python -m pytest -q tests/unit/test_engines.py` |
| Backend layer boundaries | `.venv/bin/python -m pytest -q tests/unit/test_module_dependencies.py` |
| Lint a Python file | `.venv/bin/ruff check src/vn_stream/engines/registry.py` |
| TypeScript and browser module boundaries | `npm --prefix web run check` |
| Update and verify API types | `npm --prefix web run contract:generate && npm --prefix web run contract:check` |
| Browser behavior with a built UI and Chromium installed | `(cd web && npx playwright test tests/runtime-candidates.spec.ts)` |

## Repository rules

- Use the built-in engine registry for family IDs, directory names and capabilities. Enforce capability checks on the server as well as in the UI. [Registry](src/vn_stream/engines/registry.py)
- Inspect game archives and saves statically. Use self-authored projects and isolated game and data directories for tests that write files.
- Route changes to game progress through the save operations that check revisions, back up confirmed copies and record recovery state. [Save operations](src/vn_stream/saves/operations.py)
- Commit the exported [API schema](web/api/openapi-v2.json) and [generated TypeScript types](web/src/api/generated/types.gen.ts) together when the contract changes.
- Write comments and docstrings in English. Put user-facing messages in the shared [English and Simplified Chinese resources](src/vn_stream/i18n/locales).
