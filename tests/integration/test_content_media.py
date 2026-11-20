"""Real extracted media, transcoding, Range and save upload through isolated HTTPS."""

import io
import os
import pickle
import time
import zipfile

import pytest

from tests.auth_helpers import api_data
from tests.integration.test_container import ContainerConnection

pytestmark = pytest.mark.skipif(
    not os.environ.get("VN_STREAM_CONTAINER_URL"), reason="isolated container required"
)


def test_extracted_media_and_large_save(tmp_path):
    folder = os.environ["VN_STREAM_CONTENT_FIXTURE"]
    c = ContainerConnection()
    root = "/api/v2"
    game = api_data(c.client.post(root + "/projects", json={"path": "/projects/" + folder}))
    prefix = root + "/projects/" + game["project_id"]
    try:
        job = api_data(
            c.client.post(
                prefix + "/assets/extract", json={"request_id": "content-" + game["project_id"]}
            )
        )
        for _ in range(500):
            status = api_data(c.client.get(root + "/tasks/assets/" + job["task_id"]))
            if status["state"] != "running":
                break
            time.sleep(0.1)
        assert status["state"] == "completed", status
        archived = api_data(c.client.get(prefix + "/assets/entries?category=other&folder=large"))[
            "entries"
        ][0]
        assert archived["size_bytes"] == 6 * 1024**2
        assert (
            c.client.get(
                prefix + "/assets/" + archived["id"] + "/content", headers={"Range": "bytes=0-15"}
            ).content
            == b"x" * 16
        )

        listing = api_data(c.client.get(prefix + "/assets/entries?category=video"))
        video = next(v for v in listing["entries"] if not v["directory"])
        assert (
            c.client.get(
                prefix + "/assets/" + video["id"] + "/content", headers={"Range": "bytes=0-127"}
            ).status_code
            == 206
        )
        assert c.anonymous.get(prefix + "/assets/" + video["id"] + "/content").status_code == 401
        image = api_data(c.client.get(prefix + "/assets/entries?category=image"))["entries"][0]
        assert (
            c.client.get(prefix + "/assets/" + image["id"] + "/thumbnail").headers["content-type"]
            == "image/jpeg"
        )
        detail = api_data(c.client.get(prefix + "/assets/" + video["id"]))
        assert detail["position"] == detail["total"] == 1
        for _ in range(200):
            poster = c.client.get(prefix + "/assets/" + video["id"] + "/thumbnail")
            if poster.status_code != 202:
                break
            time.sleep(0.1)
        assert poster.status_code == 200, poster.text[:120] if poster.status_code != 200 else ""
        assert poster.content.startswith(b"\xff\xd8")
        assert (
            c.client.get(prefix + "/assets/" + video["id"] + "/thumbnail").content == poster.content
        )
        assert c.client.post(prefix + "/assets/" + video["id"] + "/preview").status_code == 202
        for _ in range(300):
            preview = api_data(c.client.get(prefix + "/assets/" + video["id"] + "/preview"))
            if preview["state"] != "running":
                break
            time.sleep(0.1)
        assert preview["state"] == "completed", preview
        assert (
            c.client.get(
                prefix + "/assets/" + video["id"] + "/preview/content",
                headers={"Range": "bytes=0-127"},
            ).status_code
            == 206
        )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as z:
            z.writestr("log", pickle.dumps(({"store.flag": False}, {}), 5))
            z.writestr("json", "{}")
            z.writestr("custom", b"0" * (10 * 1024**2))
        upload = c.client.post(
            prefix + "/saves/upload?name=content-LT1.save",
            content=output.getvalue(),
            headers={"Content-Type": "application/octet-stream"},
            timeout=30,
        )
        assert upload.status_code == 200, upload.text[:200]
        uid = api_data(upload)["id"]
        view = api_data(c.client.get(prefix + "/saves/" + uid + "/variables"))
        field = view["fields"][0]
        changed = c.client.patch(
            prefix + "/saves/" + uid + "/variables",
            json={"revision": view["revision"], "changes": [dict(field, value=True)]},
        )
        assert changed.status_code == 200, changed.text
        assert (
            api_data(c.client.get(prefix + "/saves/" + uid + "/variables"))["fields"][0]["value"]
            is True
        )
        assert (
            c.client.request(
                "DELETE", prefix + "/saves/" + uid, json={"revision": api_data(changed)["revision"]}
            ).status_code
            == 200
        )
        backups = api_data(c.client.get(prefix + "/saves/" + uid + "/backups"))
        assert backups
        assert (
            c.client.post(
                prefix + "/saves/" + uid + "/backups/" + backups[-1]["id"] + "/restore",
                json={"revision": None},
            ).status_code
            == 200
        )
    finally:
        deleted = c.client.delete(prefix)
        if deleted.status_code == 202:
            for _ in range(200):
                if (
                    api_data(
                        c.client.get(
                            root + "/tasks/project-deletions/" + api_data(deleted)["task_id"]
                        )
                    )["state"]
                    != "running"
                ):
                    break
                time.sleep(0.05)
        c.client.close()
        c.anonymous.close()
