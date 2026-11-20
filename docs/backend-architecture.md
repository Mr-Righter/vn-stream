# Backend architecture

VN Stream runs one FastAPI application and one active game session. A request can finish while a worker continues an import or save migration. SQLite records what the worker has committed so the next service process can recover interrupted work. [Application assembly](../src/vn_stream/application/bootstrap.py)

## Follow an archive import

1. After uploading an archive, the browser posts its upload ID and a request ID to `/api/v2/imports`. The route validates that exactly one source was supplied and returns a task with HTTP 202. The client can query that task after a refresh. [Import routes](../src/vn_stream/api/routes/imports.py)
2. The import worker records the request in SQLite and reserves a staging directory on the game library's filesystem. Extraction rejects escaping paths and symlinks and bounds the total bytes, file count and depth. Normalization finds the project root, then the engine registry inspects it statically. [Import jobs](../src/vn_stream/imports/jobs.py) · [Archive inspection and extraction](../src/vn_stream/imports/archive.py)
3. Publication chooses a fresh destination under the detected engine's directory. It records the intended project ID and path **before** renaming the staged directory. The catalog entry and job registration then commit in one database transaction. A writable server directory already in the correct location can go straight to registration. [Import publication](../src/vn_stream/imports/publication.py)
4. If the process stops between the rename and registration, recovery examines the reserved project and its identity at the destination. It can complete registration through the same publication code. When changing this sequence, test both sides of the rename. [Import recovery](../src/vn_stream/imports/jobs.py) · [Import job tests](../tests/unit/test_import_jobs.py)

## Where a change belongs

| Responsibility | Owner |
| --- | --- |
| HTTP validation, authentication, serialization and user-facing errors | [API](../src/vn_stream/api) |
| Connecting services, ordering recovery and closing every owned worker | [Application](../src/vn_stream/application/bootstrap.py) |
| Project identity, catalog queries, maintenance checks and deletion | [Projects](../src/vn_stream/projects) |
| Asset indexes that reference original files, snapshot-scoped caches and authenticated Range reads | [Assets](../src/vn_stream/assets) and [content routes](../src/vn_stream/api/routes/content.py) |
| Database transactions, safe paths, filesystem operations and the service lease | [Storage](../src/vn_stream/storage) |

Keep FastAPI types at the API boundary. Pass dependencies explicitly from application assembly. The backend dependency tests catch HTTP imports in business modules, business imports in storage and cycles across modules. [Dependency tests](../tests/unit/test_module_dependencies.py)

## Writes, cancellation and recovery

- **Check ownership while holding the project lock.** The maintenance coordinator asks workers who owns a project. A save write checks the active session and conflicting owners. For operations needing all three locks, acquire the import operation guard, then the catalog lock, then open the SQLite transaction. [Maintenance checks](../src/vn_stream/projects/maintenance.py) · [Maintenance tests](../tests/unit/test_maintenance.py)
- **Drain submitted filesystem work on cancellation.** Async upload handlers may be cancelled while a thread holds an open file. Wait for its write before rolling back or closing the handle. Startup and shutdown also move synchronous work off the event loop. [Filesystem calls](../src/vn_stream/storage/async_io.py) · [Application lifespan](../src/vn_stream/application/lifecycle.py)
- **Treat a save edit as a multi-file operation.** Resolve every confirmed copy of the slot, check its revision, back up existing copies, record the operation, then replace each file atomically. The journal identifies the primary backup and the paths needed for rollback. Ren'Py variable editing uses a bounded static codec that preserves unrelated game state. [Save operations](../src/vn_stream/saves/operations.py) · [Ren'Py codec](../src/vn_stream/saves/renpy/pickle_codec.py) · [Batch migrations](../src/vn_stream/saves/migrations.py)

Game resources, progress and the project's `.vn-stream/` directory live together under the game root. The service keeps its database, tasks and independent save backups under its data root. Preserve both roots when testing changes to recovery or relocation. [Service paths](../src/vn_stream/storage/paths.py) · [Project identity](../src/vn_stream/projects/manifest.py) · [Offline relocation](../src/vn_stream/projects/relocation.py)
