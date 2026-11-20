# Development and release

## Local setup

Use Python 3.11 or later and Node.js 24. From the repository root, install the locked dependencies and build the browser assets. [Python project](../pyproject.toml) · [Python lock](../requirements.lock) · [Web scripts](../web/package.json)

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
npm --prefix web ci
npm --prefix web run build
```

For library and API development, start a local instance with disposable game and data directories. Open `http://127.0.0.1:8000/` and sign in as `player` with the development password supplied below. Use the container when testing streaming. [Local server entry point](../src/vn_stream/__main__.py)

```sh
mkdir -p var/dev/games
VN_STREAM_PASSWORD=dev-only .venv/bin/python -m vn_stream \
  --root var/dev/games --data var/dev/data --web web/dist
```

| Docker entry | When to use it |
| --- | --- |
| [Root Compose](../compose.yaml) | Run the published GHCR image. Set `PASSWORD` in the service environment. The [README](../README.md) covers user setup. |
| [Development Compose](../containers/compose.yaml) | Build from the checkout with local test data. For a separate Compose project on loopback port 18443, run `VN_STREAM_PORT=18443 VN_STREAM_PASSWORD=dev-only docker compose -p vn-stream-dev -f containers/compose.yaml up --build -d`. |
| [GPU overlay](../containers/compose.gpu.yaml) and [HTTPS overlay](../containers/compose.https.yaml) | Select an overlay with an additional `-f`. The GPU overlay maps the host's `/dev/dri`. The HTTPS overlay expects the bind address and directory containing the server certificate and key. |

## Choose checks for the change

| Change | Check from the repository root |
| --- | --- |
| Python behavior | Run a test file directly, e.g. `.venv/bin/python -m pytest -q tests/unit/test_import_jobs.py`. The release unit suite uses `.venv/bin/python -m pytest -q --hypothesis-profile=ci tests/unit`. |
| API model | Run `npm --prefix web run contract:generate`, then `npm --prefix web run contract:check` and `npm --prefix web run check`. |
| Browser behavior | Build the web assets, then run `(cd web && npx playwright install chromium && npx playwright test tests/runtime-candidates.spec.ts)`. The test server uses `var/e2e/` and port 18081. |
| Container integration | Point `VN_STREAM_CONTAINER_URL` and `VN_STREAM_CONTAINER_ENV` at an isolated instance and its test credentials. See each suite for its project fixtures. Run `npm --prefix web run test:container` or the relevant Python integration test. [Container browser tests](../web/tests-container) · [Integration suites](../tests/integration) |
| Runtime compatibility | Use the corresponding [real-engine suite](../tests/compatibility). For the Ren'Py smoke test, configure `VN_STREAM_RUNTIME_SMOKE=1`, the installed SDK location in `VN_STREAM_RUNTIME_ROOT` and private X11 test tools, then run `.venv/bin/python -m pytest -q tests/compatibility/test_runtime_smoke.py`. |

The [release workflow](../.github/workflows/publish.yml) specifies the Ruff and mypy targets. Report which opt-in checks ran and which dependencies were unavailable. Use self-authored test projects for writes, including restore, delete and migration.

## Release image

1. **Prepare the source.** For a filtered export from a private development checkout, run `python3 scripts/export-public-tree.py --output var/public-checkout` with a fresh destination. The tool reads a committed revision and includes both READMEs, the agent instructions, the four developer guides and the permitted source and notices. Review the export before publishing. [Public export rules](../scripts/export-public-tree.py)
2. **Prepare the release inputs.** Match the Git tag `vX.Y.Z` to the Python and web versions, root Compose image tag, README badges and embedded Compose examples. Attach `native-runtimes-linux-amd64.tar.gz` and `runtime-sources-linux-amd64.tar.gz` to the draft GitHub release. The bundle tool verifies the paired archives and locked build inputs, fetches pinned Ren'Py SDKs and assembles runtimes, notices and provenance. [Bundle tool](../scripts/prepare-runtime-bundle.py) · [Native build lock](../containers/runtime-bundle.lock.json) · [SDK releases](../src/vn_stream/runtimes/releases.py)
3. **Build and verify.** Publishing a non-prerelease GitHub release triggers the workflow. The application Dockerfile builds the Python wheel, esbuild assets and TLG decoder on the pinned Selkies base. A second Dockerfile adds the prepared runtime bundle to that image. CI starts the Linux AMD64 image with fresh storage, checks the installed runtimes and publishes the version tag to GHCR. [Application image](../containers/Dockerfile) · [Release image](../containers/release.Dockerfile) · [Image check](../scripts/check-release-image.py)
4. **Promote and deploy.** After accepting the versioned image, use the workflow's manual `promote_version` input for `latest`. It checks an anonymous pull and verifies both tags resolve to the same digest. For deployment, check the active session, uploads, imports and maintenance jobs first. Keep the previous image digest and mount configuration for rollback. Stop the old instance before another writes to the same games or data. [Publish and promotion workflow](../.github/workflows/publish.yml)

Verify login, media and input, reconnect, and save/load across container replacement in an isolated deployment before updating a live instance. When a release changes directory layout, inspect an offline relocation preview and its rollback plan before moving data. [Relocation command](../src/vn_stream/projects/relocation.py)
