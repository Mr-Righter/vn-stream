"""Explicit dependency assembly for the modular monolith."""

from dataclasses import dataclass
from functools import partial

from vn_stream.assets.operations import Assets
from vn_stream.imports.jobs import ImportJobs
from vn_stream.projects.catalog import ProjectCatalog
from vn_stream.projects.deletions import Deletions
from vn_stream.projects.queries import ProjectQueries
from vn_stream.saves.migrations import SaveMigrations
from vn_stream.saves.operations import Saves
from vn_stream.saves.signing import SigningJobs
from vn_stream.sessions.supervisor import SessionManager
from vn_stream.storage.leases import ServiceLease

from .game_launcher import GameLauncher


@dataclass
class Services:
    catalog: ProjectCatalog
    sessions: SessionManager
    imports: ImportJobs
    deletions: Deletions
    assets: Assets
    saves: Saves
    queries: ProjectQueries
    launcher: GameLauncher
    lease: ServiceLease

    def start(self):
        self.deletions.recover()
        self.imports.recover()
        self.assets.recover()
        self.saves.migrations.start()
        self.saves.signing_jobs.start()

    def close(self):
        errors = []
        for close in (
            self.sessions.stop,
            self.imports.close,
            self.deletions.close,
            self.saves.signing_jobs.close,
            self.saves.migrations.close,
            self.assets.close,
            self.lease.close,
        ):
            try:
                close()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("Service shutdown failed", errors)


def build_services(data, roots, *, library_root=None, discovery_roots=None):
    lease = ServiceLease(data)
    try:
        return _assemble(data, roots, lease, library_root, discovery_roots)
    except BaseException:
        lease.close()
        raise


def _assemble(data, roots, lease, library_root, discovery_roots):
    catalog = ProjectCatalog(
        data, roots, library_root=library_root, discovery_roots=discovery_roots
    )
    sessions = SessionManager(catalog.paths.session)
    imports = ImportJobs(catalog, recover=False)
    deletions = Deletions(catalog, sessions, imports, recover=False)
    saves = Saves(catalog, sessions, imports)
    saves.migrations = SaveMigrations(saves)
    saves.signing_jobs = SigningJobs(saves)
    assets = Assets(catalog, recover=False)
    queries = ProjectQueries(catalog, deletions)
    imports.describe = queries.describe_project
    imports.fallback = partial(queries.describe_project, minimal=True)
    catalog.on_registered = saves.signing_jobs.ensure
    imports.after_register = saves.signing_jobs.imported
    saves.signing_jobs.on_completed = imports.resume_signing
    return Services(
        catalog,
        sessions,
        imports,
        deletions,
        assets,
        saves,
        queries,
        GameLauncher(catalog, sessions, data / "runtimes"),
        lease,
    )
