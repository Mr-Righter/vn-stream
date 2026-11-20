"""Authenticated batch migration/undo over HTTPS, limited to disposable fixtures."""

import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from tests.auth_helpers import api_data
from tests.integration.test_container import ContainerConnection
from tests.integration.test_save_polish_https import sample

pytestmark = pytest.mark.skipif(
    not os.environ.get("VN_STREAM_CONTAINER_URL"), reason="isolated container required"
)


def wait(client, task):
    for _ in range(300):
        response = client.get("/api/v2/tasks/save-migrations/" + task["task_id"])
        assert response.status_code == 200
        value = api_data(response)
        if value["state"] not in {"queued", "running"}:
            return value
        time.sleep(0.05)
    pytest.fail("Migration task timed out")


def test_https_migration_preview_commit_undo_and_original_backup():
    connection = ContainerConnection()
    c = connection.client
    games = []
    projects = []
    root = "/api/v2"
    try:
        for title, value in (("old", 42), ("new", 5)):
            folder = "Migration-HTTPS-" + title + "-" + uuid4().hex[:8]
            project = (
                Path(os.environ.get("VN_STREAM_CONTAINER_PROJECTS", "var/container-verify/inbox"))
                / folder
            )
            (project / "game/saves").mkdir(parents=True)
            (project / "game/script.rpy").write_text("label start:\n    return\n")
            (project / "game/saves/slot.save").write_bytes(sample(value))
            response = c.post(root + "/projects", json={"path": "/projects/" + folder})
            assert response.status_code == 201, response.text
            games.append(api_data(response))
            projects.append(project)
        source, target = games
        prefix = root + "/projects/" + target["project_id"] + "/saves"
        source_path = projects[0] / "game/saves/slot.save"
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        target_uid = api_data(c.get(prefix))["saves"][0]["id"]
        original = c.get(prefix + "/" + target_uid + "/content").content
        preview = c.post(prefix + "/migrations/preview", json={"source_id": source["project_id"]})
        assert preview.status_code == 200, preview.text
        preview = api_data(preview)
        assert preview["counts"]["overwrite"] == 1
        assert connection.anonymous.get(prefix + "/migrations").status_code == 401
        body = {"preview_id": preview["id"], "request_id": uuid4().hex}
        response = c.post(prefix + "/migrations", json=body)
        assert response.status_code == 202, response.text
        task = api_data(response)
        assert api_data(c.post(prefix + "/migrations", json=body))["task_id"] == task["task_id"]
        assert wait(c, task)["state"] == "completed"
        variables = api_data(c.get(prefix + "/" + target_uid + "/variables"))
        assert variables["fields"][0]["value"] == "42"
        backups = api_data(c.get(prefix + "/" + target_uid + "/backups"))
        backup = next(b for b in backups if b["kind"] == "migration")
        assert (
            c.get(prefix + "/" + target_uid + "/backups/" + backup["id"] + "/content").content
            == original
        )
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash
        undo_prefix = root + "/tasks/save-migrations/" + task["task_id"]
        undo_preview = c.post(undo_prefix + "/undo-preview")
        assert undo_preview.status_code == 200, undo_preview.text
        response = c.post(
            undo_prefix + "/undo",
            json={"preview_id": api_data(undo_preview)["id"], "request_id": uuid4().hex},
        )
        assert response.status_code == 202, response.text
        assert wait(c, api_data(response))["state"] == "completed"
        assert (
            api_data(c.get(prefix + "/" + target_uid + "/variables"))["fields"][0]["value"] == "5"
        )
        assert api_data(c.get(prefix + "/" + target_uid))["signature"] == "Local"
        assert len(api_data(c.get(prefix + "/migrations"))) >= 2
        # Same-origin and CSRF protection applies to migration writes too.
        response = c.post(
            prefix + "/migrations/preview",
            json={"source_id": source["project_id"]},
            headers={"Origin": "https://untrusted.invalid"},
        )
        assert response.status_code == 403
        Path("var/save-migration-work").mkdir(parents=True, exist_ok=True)
        Path("var/save-migration-work/https-result.json").write_text(
            json.dumps(
                {
                    "migration": task["task_id"],
                    "source_unchanged": True,
                    "undo": True,
                    "backup_original": True,
                }
            )
        )
    finally:
        for game in reversed(games):
            response = c.delete(root + "/projects/" + game["project_id"])
            assert response.status_code == 202, response.text
            task = api_data(response)
            for _ in range(200):
                state = api_data(c.get(root + "/tasks/project-deletions/" + task["task_id"]))[
                    "state"
                ]
                if state != "running":
                    assert state == "completed"
                    break
                time.sleep(0.05)
        c.close()
        connection.anonymous.close()
