"""Durable, root-confined permanent deletion with rename-before-cleanup recovery."""

import json
import logging
import shutil
from pathlib import Path
from uuid import uuid4

from vn_stream.i18n import durable_messages, text
from vn_stream.storage.workers import WorkerGroup


class Deletions:
    def __init__(self, library, sessions, imports, *, recover=True):
        self.library, self.sessions, self.imports = library, sessions, imports
        self.workers = WorkerGroup("delete")
        if recover:
            self.recover()

    def recover(self):
        for record in self.rows():
            if record["state"] == "running":
                self._launch(record["id"])

    def rows(self):
        with self.library.db.transaction() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT record FROM project_deletions")]

    def get(self, uid):
        return next((r for r in self.rows() if r["id"] == uid), None) or self._missing()

    @staticmethod
    def _missing():
        raise KeyError(text("errors.deletionTaskNotFound"))

    def save(self, r):
        r = durable_messages(r)
        with self.library.db.transaction() as db:
            db.execute(
                "INSERT INTO project_deletions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,record=excluded.record",
                (r["id"], r["project_id"], r["state"], json.dumps(r, ensure_ascii=False)),
            )

    def _validate(self, game):
        from vn_stream.projects.locations import check_busy

        check_busy(self.library, game.project_id)
        path = Path(game.path)
        if path != path.absolute() or path.is_symlink() or path.resolve() != path:
            raise ValueError(text("errors.linkedFoldersCannotBeDeleted"))
        if not any(
            path.is_relative_to(root) for root in [*self.library.roots, self.library.library_root]
        ):
            raise ValueError(text("errors.theGameIsOutsideTheAllowedFolders"))
        protected = [
            *self.library.roots,
            self.library.library_root,
            *self.library.category_directories().values(),
            self.library.data,
            self.library.data / "runtimes",
            self.library.data / "backups",
        ]
        if any(p == path or p.is_relative_to(path) for p in protected):
            raise ValueError(text("errors.rootFoldersAndFoldersContainingServiceDataCannotBe"))
        if any(
            g.project_id != game.project_id and Path(g.path).is_relative_to(path)
            for g in self.library.list()
        ):
            raise ValueError(text("errors.theFolderContainsOtherRegisteredGames"))
        metadata = path / ".vn-stream/project.json"
        if (
            metadata.is_symlink()
            or json.loads(metadata.read_text())["project_id"] != game.project_id
        ):
            raise ValueError(text("errors.theGameFolderIdentityDoesNotMatch"))
        return path

    def create(self, project_id):
        with self.imports.operation(), self.library.lock:
            previous = next((r for r in self.rows() if r["project_id"] == project_id), None)
            if previous:
                return previous
            game = self.library.get(project_id)
            current = self.sessions.status()
            if current["state"] in ("running", "recovering") and (
                current.get("project_id") == project_id or current["state"] == "recovering"
            ):
                raise ValueError(text("errors.saveAndQuitTheGameBeforeDeletingItsProject"))
            if self.imports.active() and self.imports.active()["state"] == "running":
                raise ValueError(text("errors.aGameIsBeingImportedPleaseRetryLater"))
            for record in self.imports.rows():
                source = Path(record.get("source") or "/nonexistent-import-source")
                if (
                    record["state"] != "completed"
                    and not record.get("abandoned")
                    and (source == Path(game.path) or source.is_relative_to(Path(game.path)))
                ):
                    raise ValueError(text("errors.completeOrAbandonTheImportTaskUsingThisProject"))
            path = self._validate(game)
            stat = path.stat()
            uid = uuid4().hex
            record = dict(
                id=uid,
                project_id=project_id,
                name=game.name,
                path=str(path),
                quarantine=str(path.parent / (".delete-" + uid)),
                identity=[stat.st_dev, stat.st_ino],
                state="running",
                phase="prepared",
                error=None,
            )
            self.save(record)  # Recovery intent is durable before any filesystem mutation.
            self._launch(uid)
            return record

    def retry(self, uid):
        with self.library.lock:
            r = self.get(uid)
            if r["state"] == "failed":
                r.update(state="running", error=None)
                self.save(r)
                self._launch(uid)
            return r

    def _launch(self, uid):
        self.workers.start(uid, self._run, uid)

    def close(self):
        self.workers.close()

    def _run(self, uid):
        with self.library.lock:
            r = self.get(uid)
            path, quarantine = Path(r["path"]), Path(r["quarantine"])
            try:
                if path.parent.resolve() != path.parent:
                    raise ValueError(text("errors.theDeletionParentFolderWasReplacedWithALink"))
                if quarantine.parent != path.parent or quarantine.name != ".delete-" + uid:
                    raise ValueError(text("errors.invalidDeletionTaskPath"))
                if not quarantine.exists():
                    if r["phase"] == "prepared":
                        self._validate(self.library.get(r["project_id"]))
                        stat = path.stat()
                        if [stat.st_dev, stat.st_ino] != r["identity"]:
                            raise ValueError(text("errors.theGameFolderWasReplaced"))
                        self.library.rename_new(path, quarantine)
                    elif r["phase"] != "cleaning":
                        raise ValueError(text("errors.theDeletionFolderIsMissing"))
                if quarantine.exists():
                    stat = quarantine.lstat()
                    if quarantine.is_symlink() or [stat.st_dev, stat.st_ino] != r["identity"]:
                        raise ValueError(text("errors.theQuarantinedFolderIdentityDoesNotMatch"))
                    r["phase"] = "cleaning"
                    self.save(r)
                    shutil.rmtree(quarantine)
                with self.library.db.transaction() as db:
                    db.execute("DELETE FROM projects WHERE project_id=?", (r["project_id"],))
                metadata = self.library.data / "cache/projects"
                for suffix in (".json", ".ico", ".icon"):
                    (metadata / (r["project_id"] + suffix)).unlink(missing_ok=True)
                r.update(state="completed", phase="completed", error=None)
            except (OSError, ValueError, KeyError) as error:
                logging.getLogger(__name__).error(
                    "Game deletion failed: job=%s exception=%s errno=%s",
                    r["id"],
                    type(error).__name__,
                    getattr(error, "errno", None),
                )
                r.update(state="failed", error=text("game.deleteFailed"))
            self.save(r)
