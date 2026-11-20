import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from vn_stream.imports.jobs import ImportJobs
from vn_stream.imports.publication import import_project
from vn_stream.projects.catalog import ProjectCatalog


def package(tmp_path):
    path = tmp_path / "sample.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Project/game/script.rpy", "label start: pass")
        archive.writestr("Project/game/saves/persistent", "progress")
    return path


def settled(jobs, uid):
    for _ in range(400):
        job = jobs.get(uid)
        if job["state"] != "running":
            return job
        time.sleep(0.005)
    raise AssertionError("worker did not finish")


def test_background_idempotency_publication_and_cleanup(tmp_path):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path], library_root=tmp_path / "managed")
    jobs = ImportJobs(library)
    first = jobs.create("req1", path=str(source), delete_source=True)
    again = jobs.create("req1", path=str(source), delete_source=True)
    assert first["id"] == again["id"]
    job = settled(jobs, first["id"])
    assert job["state"] == "completed", job
    assert not source.exists()
    root = Path(job["project"]["path"])
    assert root.parent == tmp_path / "managed/renpy"
    assert (root / "game/saves/persistent").read_text() == "progress"
    assert not (root / ".vn-stream/source").exists()
    assert len(library.list()) == 1
    assert jobs.create("req1", path=str(source), delete_source=True)["id"] == first["id"]


def test_slow_extraction_does_not_block_library_or_job_status(tmp_path, monkeypatch):
    from vn_stream.imports import publication as service

    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    entered, release = threading.Event(), threading.Event()
    original = service.extract_archive

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "extract_archive", slow)
    jobs = ImportJobs(library)
    job = jobs.create("req1", path=str(source))
    assert entered.wait(1)
    try:
        assert jobs.get(job["id"])["state"] == "running"
        assert library.list() == []
        assert library.lock.acquire(blocking=False)
        library.lock.release()
        with pytest.raises(ValueError):
            jobs.create("req2", path=str(source))
    finally:
        release.set()
    assert settled(jobs, job["id"])["state"] == "completed"


def test_password_not_persisted_and_retry_keeps_source(tmp_path):
    import pyzipper

    source = tmp_path / "secret.zip"
    with pyzipper.AESZipFile(source, "w", encryption=pyzipper.WZ_AES) as archive:
        archive.setpassword(b"topsecret")
        archive.writestr("game/script.rpy", "pass")
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    job = settled(jobs, jobs.create("req", path=str(source), password="wrong")["id"])
    assert job["state"] == "failed"
    assert source.exists()
    job = settled(jobs, jobs.retry(job["id"], password="topsecret")["id"])
    assert job["state"] == "completed"
    assert b"topsecret" not in library.database.read_bytes()
    assert b"wrong" not in library.database.read_bytes()


def test_recover_published_manifest_before_database_commit(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    original = jobs.publication.record

    def interrupted(*args, **kwargs):
        raise SystemExit("power failure")

    monkeypatch.setattr(jobs.publication, "record", interrupted)
    job = jobs.create("req", path=str(source))
    jobs.wait(job["id"])
    assert library.list() == []
    monkeypatch.setattr(jobs.publication, "record", original)
    recovered = ImportJobs(library)
    job = recovered.get(job["id"])
    assert job["state"] == "completed", job
    assert len(library.list()) == 1
    assert (
        json.loads((Path(job["project"]["path"]) / ".vn-stream/project.json").read_text())[
            "project_id"
        ]
        == job["project"]["project_id"]
    )


def test_request_id_conflicts_and_upload_alias_is_durable(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    job = settled(jobs, jobs.create("req1", path=str(source))["id"])
    assert job["state"] == "completed"
    with pytest.raises(ValueError, match="request_id"):
        jobs.create("req1", path=str(source), delete_source=True)
    with pytest.raises(ValueError, match="request_id"):
        jobs.create("req1", path=str(tmp_path / "other.zip"))

    uid = "a" * 32
    folder = library.data / "uploads/archives" / uid
    folder.mkdir(parents=True)
    (folder / "content").write_bytes(source.read_bytes())
    (folder / "meta.json").write_text(
        json.dumps({"filename": source.name, "size": source.stat().st_size})
    )
    entered, release = threading.Event(), threading.Event()
    original = jobs.publication.import_source

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs.publication, "import_source", slow)
    upload_job = jobs.create("upload1", upload_id=uid)
    assert entered.wait(1)
    try:
        assert jobs.create("upload2", upload_id=uid)["id"] == upload_job["id"]
        with pytest.raises(ValueError):
            jobs.create("upload3", upload_id=uid, delete_source=True)
    finally:
        release.set()
    assert settled(jobs, upload_job["id"])["state"] == "completed"
    assert not folder.exists()
    restarted = ImportJobs(library)
    assert restarted.create("upload2", upload_id=uid)["id"] == upload_job["id"]
    assert restarted.create("upload-after-cleanup", upload_id=uid)["id"] == upload_job["id"]
    assert not restarted.bound(uid)


def test_nas_source_change_and_delete_failure_are_warnings(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    original = jobs._cleanup

    def changed(uid):
        # Replace with a different inode only after the game+result transaction committed.
        record = jobs._read(uid)
        assert record["registered"] and library.get(record["reserved_project"]["project_id"])
        source.unlink()
        source.write_bytes(b"new content")
        original(uid)

    monkeypatch.setattr(jobs, "_cleanup", changed)
    job = settled(jobs, jobs.create("changed", path=str(source), delete_source=True)["id"])
    assert job["state"] == "completed" and job["warning"]
    assert source.read_bytes() == b"new content"

    source = package(tmp_path)
    monkeypatch.setattr(jobs, "_cleanup", original)
    unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path == source:
            raise PermissionError("source read-only")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied)
    job = settled(jobs, jobs.create("denied", path=str(source), delete_source=True)["id"])
    assert job["state"] == "completed" and job["warning"]
    assert source.exists()
    assert len(library.list()) == 2


def test_interrupt_before_publication_removes_partial_and_requires_retry_password(
    tmp_path, monkeypatch
):
    from vn_stream.imports import publication as service

    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    original = service.extract_archive

    def interrupted(source, destination, *args, **kwargs):
        destination.mkdir()
        (destination / "partial").write_bytes(b"partial")
        raise SystemExit("shutdown")

    monkeypatch.setattr(service, "extract_archive", interrupted)
    job = jobs.create("req", path=str(source), password="not-persisted")
    jobs.wait(job["id"])
    # Recreate the durable partial checkpoint that a kill -9 can leave behind.
    record = jobs._read(job["id"])
    staging = Path(record["staging"])
    staging.mkdir()
    (staging / "partial").write_bytes(b"partial")
    monkeypatch.setattr(service, "extract_archive", original)
    restarted = ImportJobs(library)
    assert restarted.get(job["id"])["state"] == "failed"
    assert not staging.exists()
    assert source.exists()
    assert library.list() == []
    assert b"not-persisted" not in library.database.read_bytes()
    assert settled(restarted, restarted.retry(job["id"])["id"])["state"] == "completed"


def test_registered_cleanup_interruption_recovers_without_reextracting(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)

    def interrupted(uid):
        assert jobs._read(uid)["registered"]
        raise SystemExit("shutdown")

    monkeypatch.setattr(jobs, "_cleanup", interrupted)
    job = jobs.create("req", path=str(source), delete_source=True)
    jobs.wait(job["id"])
    assert len(library.list()) == 1 and source.exists()

    def never(*args, **kwargs):
        raise AssertionError("must not extract a registered game again")

    monkeypatch.setattr(jobs.publication, "import_source", never)
    restarted = ImportJobs(library)
    result = restarted.get(job["id"])
    assert result["state"] == "completed"
    assert not source.exists()
    assert len(library.list()) == 1


def test_postcommit_unexpected_cleanup_error_retains_checkpoint_and_recovers(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)

    def failed(uid):
        raise OSError("cleanup storage disappeared")

    monkeypatch.setattr(jobs, "_cleanup", failed)
    job = settled(jobs, jobs.create("req", path=str(source), delete_source=True)["id"])
    assert job["state"] == "failed" and job["warning"]
    assert source.exists() and len(library.list()) == 1
    restarted = ImportJobs(library)
    assert restarted.get(job["id"])["state"] == "completed"
    assert not source.exists()


def test_publication_never_overwrites_colliding_empty_directory(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    collision = library.library_root / "existing"
    original = library.rename_new

    def raced(source, target):
        assert target == collision
        target.mkdir()
        return original(source, target)

    monkeypatch.setattr(jobs.publication, "_target", lambda *_: collision)
    monkeypatch.setattr(library, "rename_new", raced)
    job = settled(jobs, jobs.create("req", path=str(source))["id"])
    assert job["state"] == "failed"
    assert collision.is_dir() and list(collision.iterdir()) == []
    assert library.list() == []
    assert source.exists()


def test_abandon_failure_keeps_nas_source(tmp_path):
    source = tmp_path / "bad.zip"
    source.write_bytes(b"bad")
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    job = settled(jobs, jobs.create("req", path=str(source), delete_source=True)["id"])
    assert job["state"] == "failed"
    assert jobs.abandon(job["id"]) == {"abandoned": True}
    assert source.read_bytes() == b"bad"
    assert jobs.active() is None


def test_game_insert_and_job_result_roll_back_together_then_recover(tmp_path):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    with library.db.transaction() as db:
        db.execute(
            "CREATE TRIGGER interrupt_result BEFORE UPDATE ON import_jobs "
            "WHEN json_extract(NEW.record, '$.registered') = 1 "
            "BEGIN SELECT RAISE(ABORT, 'simulated transaction failure'); END"
        )
    job = settled(jobs, jobs.create("req", path=str(source))["id"])
    assert job["state"] == "failed"
    assert library.list() == []
    record = jobs._read(job["id"])
    assert not record["registered"] and record["project"] is None
    assert (Path(record["reserved_project"]["path"]) / ".vn-stream/project.json").exists()
    with library.db.transaction() as db:
        db.execute("DROP TRIGGER interrupt_result")
    restarted = ImportJobs(library)
    assert restarted.get(job["id"])["state"] == "completed"
    assert len(library.list()) == 1


def test_readonly_copy_uses_configured_root_and_preserves_home_saves_and_runtime(
    tmp_path, monkeypatch
):
    project = tmp_path / "nas/Story"
    (project / "game/saves").mkdir(parents=True)
    (project / "game/script.rpy").write_text("pass")
    (project / "game/saves/persistent").write_bytes(b"progress")
    library = ProjectCatalog(
        tmp_path / "data", [tmp_path / "nas"], library_root=tmp_path / "new-root"
    )
    original = import_project(library, project)
    library.select_runtime(original.project_id, "8.3.7")
    (project / ".vn-stream/home").mkdir()
    (project / ".vn-stream/home/keep").write_bytes(b"home-progress")
    manifest = (project / ".vn-stream/project.json").read_bytes()
    monkeypatch.setattr("vn_stream.projects.catalog.os.access", lambda *_: False)
    jobs = ImportJobs(library)
    result = settled(jobs, jobs.create("readonly", path=str(project))["id"])
    assert result["state"] == "completed", result
    copied = Path(result["project"]["path"])
    assert copied.parent == library.library_root / "renpy" and copied.name.startswith("Story-")
    assert result["project"]["project_id"] != original.project_id
    assert (copied / "game/saves/persistent").read_bytes() == b"progress"
    assert (copied / ".vn-stream/home/keep").read_bytes() == b"home-progress"
    assert library.runtime_version(result["project"]["project_id"]) == "8.3.7"
    assert (project / ".vn-stream/project.json").read_bytes() == manifest


@pytest.mark.parametrize("failure", ["permission", "invalid", "target_permission", "missing"])
def test_existing_unverified_publication_preserves_reservation_on_retry_and_abandon(
    tmp_path, monkeypatch, failure
):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    upload_id = "b" * 32
    upload = library.data / "uploads/archives" / upload_id
    upload.mkdir(parents=True)
    (upload / "content").write_bytes(source.read_bytes())
    (upload / "meta.json").write_text(
        json.dumps({"filename": source.name, "size": source.stat().st_size})
    )
    original_record = jobs.publication.record

    def interrupted(*args, **kwargs):
        raise SystemExit("rename succeeded, DB transaction not committed")

    monkeypatch.setattr(jobs.publication, "record", interrupted)
    job = jobs.create("interrupted", upload_id=upload_id)
    jobs.wait(job["id"])
    reservation = jobs._read(job["id"])["reserved_project"]
    target = Path(reservation["path"])
    manifest = target / ".vn-stream/project.json"
    original_manifest = manifest.read_text()
    original_read = Path.read_text
    original_lstat = Path.lstat
    monkeypatch.setattr(jobs.publication, "record", original_record)

    def unreadable(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("temporary NAS permission failure")
        return original_read(path, *args, **kwargs)

    def target_unreadable(path, *args, **kwargs):
        if path == target:
            raise PermissionError("cannot determine target state")
        return original_lstat(path, *args, **kwargs)

    if failure == "permission":
        monkeypatch.setattr(Path, "read_text", unreadable)
    elif failure == "target_permission":
        monkeypatch.setattr(Path, "lstat", target_unreadable)
    elif failure == "missing":
        manifest.unlink()
    else:
        manifest.write_text("{invalid manifest")
    recovered = ImportJobs(library)
    assert recovered.get(job["id"])["state"] == "failed"
    assert recovered.get(job["id"])["error"]

    with pytest.raises(ValueError):
        recovered.retry(job["id"])
    with pytest.raises(ValueError):
        recovered.abandon(job["id"])
    record = recovered._read(job["id"])
    assert record["reserved_project"] == reservation
    assert not record["abandoned"] and record["project"] is None
    assert recovered.bound(upload_id)
    assert (upload / "content").exists()
    assert [
        p for p in (library.library_root / "renpy").iterdir() if not p.name.startswith(".")
    ] == [target]
    assert library.list() == []

    monkeypatch.setattr(Path, "read_text", original_read)
    monkeypatch.setattr(Path, "lstat", original_lstat)
    manifest.write_text(original_manifest)
    result = recovered.retry(job["id"])
    assert result["state"] == "completed"
    assert result["project"]["project_id"] == reservation["project_id"]
    assert result["project"]["path"] == str(target)
    assert len(library.list()) == 1
    assert not upload.exists()
    assert [
        p for p in (library.library_root / "renpy").iterdir() if not p.name.startswith(".")
    ] == [target]


def test_reserved_but_definitely_unpublished_job_can_extract_again(tmp_path, monkeypatch):
    source = package(tmp_path)
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    jobs = ImportJobs(library)
    rename = library.rename_new

    def interrupted(*args, **kwargs):
        raise SystemExit("before rename")

    monkeypatch.setattr(library, "rename_new", interrupted)
    job = jobs.create("before-publication", path=str(source))
    jobs.wait(job["id"])
    reservation = jobs._read(job["id"])["reserved_project"]
    assert not Path(reservation["path"]).exists()
    monkeypatch.setattr(library, "rename_new", rename)
    recovered = ImportJobs(library)
    result = settled(recovered, recovered.retry(job["id"])["id"])
    assert result["state"] == "completed"
    assert len(library.list()) == 1
    assert len([p for p in library.library_root.iterdir() if not p.name.startswith(".")]) == 1


def test_directory_abandon_reenters_after_cleanup_before_job_commit(tmp_path, monkeypatch):
    library = ProjectCatalog(tmp_path / "data", [], library_root=tmp_path / "managed")
    jobs = ImportJobs(library)
    upload = jobs.directories.create("Story", 1, 0)
    upload_id = upload["id"]
    jobs.directories.manifest(upload_id, "one", [{"path": "game/unknown.bin", "size": 0}])
    jobs.directories.seal(upload_id)
    jobs.directories.complete(upload_id)
    job_id = jobs.create("bad-directory", directory_upload_id=upload_id)["id"]
    assert jobs.wait(job_id)["state"] == "failed"
    unrelated = library.library_root / "keep-save"
    unrelated.write_bytes(b"preserve")
    original_update = jobs.repository.update

    def interrupt_final_commit(uid, **updates):
        if updates.get("abandoned"):
            with pytest.raises(KeyError):
                jobs.directories.record(upload_id)
            assert not (jobs.directories.root / upload_id).exists()
            raise SystemExit("cleanup committed, abandoned flag not committed")
        return original_update(uid, **updates)

    monkeypatch.setattr(jobs.repository, "update", interrupt_final_commit)
    with pytest.raises(SystemExit):
        jobs.abandon(job_id)
    monkeypatch.setattr(jobs.repository, "update", original_update)
    restarted = ImportJobs(library)
    assert restarted.get(job_id)["state"] == "failed"
    assert not restarted.get(job_id)["abandoned"]
    assert restarted.active()["id"] == job_id
    assert restarted.abandon(job_id) == {"abandoned": True}
    assert restarted.get(job_id)["abandoned"]
    assert not restarted.bound_directory(upload_id)
    assert restarted.active() is None
    assert unrelated.read_bytes() == b"preserve"


@pytest.mark.parametrize("valid", [False, True])
def test_directory_progress_tracks_validation_before_publication_and_cleanup(
    tmp_path, monkeypatch, valid
):
    library = ProjectCatalog(tmp_path / "data", [], library_root=tmp_path / "managed")
    jobs = ImportJobs(library)
    upload_id = jobs.directories.create("Story", 1, 0)["id"]
    path = "game/script.rpy" if valid else "game/unknown.bin"
    jobs.directories.manifest(upload_id, "one", [{"path": path, "size": 0}])
    jobs.directories.seal(upload_id)
    jobs.directories.complete(upload_id)
    source = jobs.directories.source(upload_id)
    observed = []
    prepare, rename, cleanup = library.prepare_project, library.rename_new, jobs._cleanup

    def validating(*args, **kwargs):
        observed.append(("validation", jobs.active()["phase"]))
        assert library.list() == [] and (source / path).is_file()
        return prepare(*args, **kwargs)

    def publishing(*args, **kwargs):
        observed.append(("publication", jobs.active()["phase"]))
        assert library.list() == []
        return rename(*args, **kwargs)

    def cleaning(uid):
        observed.append(("cleanup", jobs.get(uid)["phase"]))
        assert len(library.list()) == 1
        return cleanup(uid)

    monkeypatch.setattr(library, "prepare_project", validating)
    monkeypatch.setattr(library, "rename_new", publishing)
    monkeypatch.setattr(jobs, "_cleanup", cleaning)
    job_id = jobs.create("directory-phases", directory_upload_id=upload_id)["id"]
    job = jobs.wait(job_id)
    expected = [("validation", "validating")] if valid else []
    if valid:
        expected += [("publication", "registering"), ("cleanup", "cleaning")]
        assert job["state"] == "completed"
        assert (Path(job["project"]["path"]) / path).is_file()
        assert not source.exists()
    else:
        assert job["state"] == "failed"
        assert (source / path).is_file()
        assert library.list() == []
        assert jobs.bound_directory(upload_id)
    assert observed == expected
