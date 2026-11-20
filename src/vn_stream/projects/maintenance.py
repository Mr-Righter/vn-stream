"""A project claim seam; each worker owns its durable claim representation."""

from collections.abc import Callable, Iterable

from vn_stream.i18n import text


class ProjectMaintenance:
    def __init__(self):
        self.sources: dict[str, Callable[[str], Iterable[str]]] = {}
        self.session: Callable[[], dict] | None = None

    def register(self, kind: str, claims: Callable[[str], Iterable[str]]) -> None:
        self.sources[kind] = claims

    def bind_session(self, reader: Callable[[], dict]) -> None:
        self.session = reader

    def check(
        self,
        project_id: str,
        *,
        include_session=False,
        include_imports=False,
        signing_owner=None,
        migration_owner=None,
        import_owner=None,
        operation_owners=(),
    ) -> None:
        if include_session and self.session:
            session = self.session()
            if session["state"] == "recovering" or (
                session["state"] == "running" and session.get("project_id") == project_id
            ):
                raise ValueError(text("saves.stopFirst"))
        owners = {"signing": signing_owner, "migration": migration_owner, "imports": import_owner}
        for kind, claims in self.sources.items():
            if kind == "imports" and not include_imports:
                continue
            if any(
                owner != owners.get(kind)
                and not (kind == "operations" and owner in operation_owners)
                for owner in claims(project_id)
            ):
                raise ValueError(text("content.busy"))
