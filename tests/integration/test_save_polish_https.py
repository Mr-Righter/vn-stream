"""Real HTTPS signing, repeatable backups and restore on self-authored fixtures only."""

import io
import os
import pickle
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from tests.auth_helpers import api_data
from tests.integration.test_container import ContainerConnection

pytestmark = pytest.mark.skipif(
    not os.environ.get("VN_STREAM_CONTAINER_URL"), reason="isolated container required"
)


def sample(value):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("log", pickle.dumps(({"store.money": value}, {}), 2))
        archive.writestr("json", "{}")
    return output.getvalue()


def test_uploaded_and_project_saves_sign_and_manual_backups_restore():
    name = "Save-Polish-API-" + uuid4().hex[:8]
    project = (
        Path(os.environ.get("VN_STREAM_CONTAINER_PROJECTS", "var/container-verify/inbox")) / name
    )
    (project / "game/saves").mkdir(parents=True)
    (project / "game/script.rpy").write_text("label start:\n    return\n")
    original = sample(5)
    (project / "game/saves/slot.save").write_bytes(original)
    connection = ContainerConnection()
    c = connection.client
    prefix = None
    try:
        imported = c.post("/api/v2/projects", json={"path": "/projects/" + name})
        assert imported.status_code == 201, imported.text
        prefix = "/api/v2/projects/" + api_data(imported)["project_id"] + "/saves"
        signing = api_data(c.get(prefix + "/signing"))
        assert signing["state"] == "completed" and signing["signed"] == 1
        uid = api_data(c.get(prefix))["saves"][0]["id"]
        target = prefix + "/" + uid
        variables = api_data(c.get(target + "/variables"))
        assert variables["editable"] and variables["fields"][0]["value"] == "5"
        backups = []
        for _ in range(2):
            body = {"revision": variables["revision"], "request_id": uuid4().hex}
            first = c.post(target + "/backups", json=body)
            assert first.status_code == 201, first.text
            assert api_data(c.post(target + "/backups", json=body))["id"] == api_data(first)["id"]
            backups.append(api_data(first))
        assert backups[0]["id"] != backups[1]["id"]
        history = api_data(c.get(target + "/backups"))
        assert [b["id"] for b in history[:2]] == [b["id"] for b in reversed(backups)]
        raw_backup = next(b for b in history if b["kind"] == "sign")
        downloaded = c.get(target + "/backups/" + raw_backup["id"] + "/content")
        assert downloaded.content == original
        assert "slot.save" in downloaded.headers["content-disposition"]
        replacement = c.post(
            prefix + "/upload?name=slot.save&revision=" + variables["revision"],
            content=sample(99),
            headers={"Content-Type": "application/octet-stream"},
        )
        assert replacement.status_code == 200, replacement.text
        current = api_data(c.get(target + "/variables"))
        assert current["editable"] and current["fields"][0]["value"] == "99"
        restored = c.post(
            target + "/backups/" + backups[0]["id"] + "/restore",
            json={"revision": current["revision"]},
        )
        assert restored.status_code == 200, restored.text
        current = api_data(c.get(target + "/variables"))
        assert current["editable"] and current["fields"][0]["value"] == "5"
        assert api_data(c.get(target))["signature"] == "Local"
        assert connection.anonymous.get(prefix + "/signing").status_code == 401
    finally:
        if prefix:
            deletion = c.delete(prefix.rsplit("/saves", 1)[0])
            assert deletion.status_code == 202
            for _ in range(200):
                if (
                    api_data(
                        c.get("/api/v2/tasks/project-deletions/" + api_data(deletion)["task_id"])
                    )["state"]
                    != "running"
                ):
                    break
                time.sleep(0.05)
        c.close()
        connection.anonymous.close()
