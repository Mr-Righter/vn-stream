"""Cancellation must not race a filesystem worker against descriptor cleanup."""

import asyncio
import threading

import pytest

from vn_stream.storage.async_io import filesystem_call


def test_cancelled_write_finishes_before_rollback_and_close(tmp_path):
    entered, release = threading.Event(), threading.Event()
    file = (tmp_path / "upload").open("wb")
    events = []

    def write():
        entered.set()
        assert release.wait(5)
        file.write(b"unfinished")
        events.append("written")

    async def request():
        try:
            await filesystem_call(write)
        except asyncio.CancelledError:
            file.truncate(0)
            events.append("rollback")
            raise
        finally:
            file.close()
            events.append("closed")

    async def scenario():
        task = asyncio.create_task(request())
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert not file.closed
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation was swallowed")

    asyncio.run(scenario())
    assert events == ["written", "rollback", "closed"]
    assert (tmp_path / "upload").read_bytes() == b""


def test_cancelled_directory_commit_keeps_bytes_and_durable_offset_consistent(
    tmp_path, monkeypatch
):
    import asyncio
    from threading import Event

    from fastapi import Request

    from vn_stream.api.routes.directory_uploads import directory_upload_routes
    from vn_stream.imports.jobs import ImportJobs
    from vn_stream.projects.catalog import ProjectCatalog

    jobs = ImportJobs(ProjectCatalog(tmp_path / "data", [tmp_path]), recover=False)
    uploads = jobs.directories
    upload = uploads.create("Story", 1, 4)["id"]
    entry = uploads.manifest(upload, "first", [{"path": "game/script.rpy", "size": 4}])["files"][0]
    uploads.seal(upload)
    endpoint = next(
        route.endpoint for route in directory_upload_routes(jobs).routes if route.methods == {"PUT"}
    )
    entered, release = Event(), Event()
    original = uploads.commit_append

    def commit(*args):
        result = original(*args)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(uploads, "commit_append", commit)

    async def scenario():
        async def receive():
            return {"type": "http.request", "body": b"test", "more_body": False}

        request = Request({"type": "http", "method": "PUT", "headers": []}, receive)
        task = asyncio.create_task(endpoint(upload, entry["id"], request, 0))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
        assert uploads.file(upload, entry["id"])["offset"] == 4
        assert uploads.file_path(upload, entry).read_bytes() == b"test"
        assert uploads.complete(upload)["state"] == "complete"
    finally:
        release.set()
        jobs.close()


def test_cancelled_acquisition_closes_its_unclaimed_file():
    from io import BytesIO
    from threading import Event

    from vn_stream.storage.async_io import filesystem_resource

    entered, release = Event(), Event()
    stream = BytesIO()

    def acquire():
        entered.set()
        assert release.wait(3)
        return stream

    async def scenario():
        async def request():
            async with filesystem_resource(acquire, lambda file: file.close()):
                pytest.fail("Cancelled request must not use the acquired resource")

        task = asyncio.create_task(request())
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert stream.closed
