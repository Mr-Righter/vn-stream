# Frontend and API v2

## Browser entry points

| Entry | Responsibilities |
| --- | --- |
| [Library](../web/src/main.tsx) | Creates the library and import controllers once. The application subscribes to their snapshots and uses the URL hash for library, project, media and save navigation. |
| [Login](../web/src/login.tsx) | Handles the sign-in form and returns the browser to its destination. |
| [Game controls](../web/src/game-controls.tsx) | Mounts the React toolbar in the Selkies page. Its controller owns VN Stream actions, session status and reconnection. Selkies owns the media surface and desktop input. |

The esbuild script packages these entries, shared code, styles, fonts and icons in `web/dist/`. The container serves the library under `/library/` and the player at `/`. Both reach the same-origin API at `/api/v2/`. Build API URLs through the shared path helper so a player's current page or hash cannot change the destination. [Web build](../web/build.mjs) · [API path helper](../web/src/api/paths.ts) · [Container routes](../containers/root/etc/nginx/vn-stream-locations.conf)

## Trace the runtime selector

1. The project query asks the engine registry which runtime builds are available. The project response includes `recommendation`, `runtime_options`, a reason for each unavailable option, and project `capabilities`. The Python schema gives the public fields their wire names and types. [Project query](../src/vn_stream/projects/queries.py) · [Project schema](../src/vn_stream/api/schemas/projects.py)
2. The library API client reads the response using the generated `Project` type. The project overview renders the recommendation, disables unavailable builds and translates their reasons. Game details uses the capabilities to decide which tabs to offer. [Library API](../web/src/api/library.ts) · [Runtime selector](../web/src/features/project-details/project-overview.tsx) · [Game details](../web/src/features/project-details/game-details.tsx)
3. On Play, the library controller checks the session. To start a game, it submits the chosen `runtime_id`, or `null` for automatic selection, to `POST /api/v2/projects/{project_id}/start`. The backend checks availability again. After a successful start or resume, the controller navigates the current tab to the stream URL. [Library controller](../web/src/features/library/library-controller.ts) · [Launcher](../src/vn_stream/application/game_launcher.py)

### When a public field changes

Edit the FastAPI response or request model and its producer. From the repository root, run the generator, verify the result, then typecheck its consumers.

```sh
npm --prefix web run contract:generate
npm --prefix web run contract:check
npm --prefix web run check
```

The generator exports the running application's schema using temporary data and derives TypeScript from it. Commit both generated files with the API change. The JSON client unwraps `data` and optional `pagination` and retains the request ID on API errors. File downloads and Range media use binary responses. The player control WebSocket has a separate message contract. [Generator](../web/generate-contract.mjs) · [OpenAPI](../web/api/openapi-v2.json) · [Generated types](../web/src/api/generated/types.gen.ts) · [JSON client](../web/src/api/client.ts) · [Player protocol](../web/src/api/player.ts)

## State, permissions and presentation

- Put transport and endpoint wrappers in `web/src/api/`. Authenticated calls use the session cookie and include a CSRF token on mutations. Enforce the capability and permission again in the backend route. [Browser authentication](../web/src/api/auth.ts) · [Content routes](../src/vn_stream/api/routes/content.py)
- Keep long-lived uploads and polling with their controllers. Keep save drafts with the editor and run its leave check on navigation. A theme or language change should rerender labels while preserving selection, draft values and active operations. [Library shell](../web/src/app/library-app.tsx) · [Import controller](../web/src/features/imports/import-controller.ts) · [Save editor](../web/src/features/saves/save-editor.tsx)
- For a toolbar change, use the player's supported actions to determine which controls to render and their current availability to enable them. Keep the Selkies video, pointer coordinate mapping and IME input nodes intact. [Player toolbar](../web/src/features/player/player-toolbar.tsx) · [Player viewport](../web/src/features/player/player-viewport.ts)
- Put visible labels in both locale files and derive Ant Design and CSS values from the shared theme. Check keyboard focus, mobile safe areas, narrow layouts and reduced motion when changing controls. [Locales](../src/vn_stream/i18n/locales) · [Theme](../src/vn_stream/ui/theme.json) · [UI provider](../web/src/ui/provider.tsx)

For a selector change, run the [runtime candidate browser tests](../web/tests/runtime-candidates.spec.ts). The web `check` command also enforces the [module dependency rules](../web/check-architecture.mjs).
