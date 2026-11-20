# Engine and runtime integration

An **engine** identifies a family of game formats and behaviors. A **runtime** identifies a particular executable build. For example, Yuri and Jh both launch ONScripter projects and have separate runtime IDs. The project ID stays with the game when the player switches runtime. [Registry](../src/vn_stream/engines/registry.py) · [Runtime catalog](../src/vn_stream/runtimes/catalog.py)

## Add a runtime to an existing family

1. Start with the family's launch code. In ONScripter, Yuri accepts a save-directory override, Jh follows the script's save directory and NEW has its own script and save arguments. Put a new flavor's argument handling here and describe its entry, encoding and resource requirements in the compatibility check. This check supplies the reason shown in the selector and runs again before launch. [ONS launch](../src/vn_stream/engines/ons/launch.py) · [ONS compatibility](../src/vn_stream/engines/ons/compatibility.py)
2. Give the build a distinct runtime ID and a readable display name. Record its pinned source, executable digest, required resources and licenses in the relevant packaging inputs. The catalog combines installed and bundled builds. Automatic selection considers validated builds. Explicit selection can also use eligible experimental builds. For the shipped profiles, Yuri and krkr-rs are the native defaults. [Native selection](../src/vn_stream/runtimes/native.py) · [Candidate sources](../containers/native-candidates.lock.json) · [Release bundle](../containers/runtime-bundle.lock.json)
3. Test a missing resource, an incompatible project and an explicit selection alongside the existing default. Check that a binary changed after discovery fails validation. If shipping the runtime, follow the [release procedure](development-and-release.md#release-image).

## Add an engine family

| Adapter member | What to implement |
| --- | --- |
| `engine_id`, `directory_name`, display name and capabilities | Choose a stable family ID and filesystem name. Existing examples include `ons` → `onscripter/` and `krkr` → `kirikiri/`. Advertise the features the adapter implements. |
| `inspect_project` | Read bounded static evidence and return an `Inspection`, or return `None` for an unrecognized project. Report the entry point and any reliable encoding, save-directory or canvas information. |
| `resolve_runtime` | Choose an eligible installed build from the same family. Ren'Py matches SDK versions. Native forks use build profiles and an explicit default. |
| `prepare_launch` | Return a `LaunchPlan` with command arguments, working directory, environment and display/action policies. Keep engine-specific hooks and save paths in the adapter. |

Register the adapter in `ADAPTERS`. Supply an asset provider if it can enumerate media. Project queries use registry and inspection results to populate capabilities and runtime options. The existing fourth-engine test exercises registration through the API. [Contracts](../src/vn_stream/engines/contracts.py) · [Registry](../src/vn_stream/engines/registry.py) · [Project queries](../src/vn_stream/projects/queries.py) · [Engine tests](../tests/unit/test_engines.py)

## From launch to the browser

The launcher holds the catalog lock, checks maintenance, re-inspects the project and resolves the selected runtime. It hands preparation to the single-session supervisor. The worker starts the process with the plan and tracks display readiness separately from process liveness. [Launcher](../src/vn_stream/application/game_launcher.py) · [Supervisor](../src/vn_stream/sessions/supervisor.py) · [Session worker](../src/vn_stream/sessions/worker.py)

| Family | Launch implementation | Display and controls |
| --- | --- | --- |
| Ren'Py | [Launch](../src/vn_stream/engines/renpy/launch.py) | The engine hook reports its canvas and implements native actions. |
| ONScripter | [Launch](../src/vn_stream/engines/ons/launch.py) | Uses inspected dimensions when available and observes the game window. |
| Kirikiri | [Launch](../src/vn_stream/engines/krkr/launch.py) | Builds the selected host's command and observes the game window. |

Selkies carries the media and desktop input. VN Stream's control channel handles session actions and ownership. Only a heartbeat from the current media connection and epoch renews the 60-second lease. [Window canvas](../src/vn_stream/streaming/window_canvas.py) · [Session presence](../src/vn_stream/sessions/presence.py)

## Verify a build

Use a self-authored fixture or an isolated project copy with its own progress. Check recognition, an actual rendered frame, text, audio, input alignment, exit, and a save loaded after restart. For session changes, also exercise initial connection, reconnect within the lease, and cleanup after expiry. Record which game/build combinations reached each step. [Compatibility suites](../tests/compatibility) · [Lease tests](../tests/unit/test_player_lifecycle.py)
