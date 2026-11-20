import io
import pickle
import time
import zipfile

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app


def payload():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("log", pickle.dumps(({"store.flag": False, "store.money": 2**65}, {}), 5))
        z.writestr("json", "{}")
    return out.getvalue()


def test_authenticated_assets_and_save_roundtrip(tmp_path):
    p = tmp_path / "nas/sample"
    (p / "game").mkdir(parents=True)
    (p / "game/script.rpy").write_text("label start: pass")
    (p / "game/audio").mkdir()
    (p / "game/audio/test.mp3").write_bytes(b"ID3" + b"x" * 100)
    app = create_app(tmp_path / "data", [tmp_path / "nas"], "secret")
    with TestClient(app) as c:
        sign_in(c, "secret")
        game = api_data(c.post("/api/v2/projects", json={"path": str(p)}))
        root = f"/api/v2/projects/{game['project_id']}"
        job = api_data(c.post(root + "/assets/extract", json={"request_id": "same"}))
        for _ in range(200):
            state = api_data(c.get("/api/v2/tasks/assets/" + job["task_id"]))
            if state["state"] != "running":
                break
            time.sleep(0.01)
        assert state["state"] == "completed", state
        entry = api_data(c.get(root + "/assets/entries?category=audio&search=test"))["entries"][0]
        assert api_data(c.get(root + "/assets/" + entry["id"] + "?search=test"))["position"] == 1
        response = c.get(
            root + "/assets/" + entry["id"] + "/content", headers={"Range": "bytes=0-2"}
        )
        assert response.status_code == 206
        assert response.content == b"ID3"
        assert (
            c.post(
                root + "/saves/upload?name=1-1-LT1.save",
                content=payload(),
                headers={"Content-Type": "application/octet-stream"},
            ).status_code
            == 200
        )
        save = api_data(c.get(root + "/saves"))["saves"][0]
        assert api_data(c.get(root + "/saves/" + save["id"]))["name"] == save["name"]
        view = api_data(c.get(root + "/saves/" + save["id"] + "/variables"))
        f = next(f for f in view["fields"] if f["name"] == "store.flag")
        result = c.patch(
            root + "/saves/" + save["id"] + "/variables",
            json={"revision": view["revision"], "changes": [dict(f, value=True)]},
        )
        assert result.status_code == 200, result.text
        assert (
            c.patch(
                root + "/saves/" + save["id"] + "/variables",
                json={"revision": view["revision"], "changes": [dict(f, value=False)]},
            ).status_code
            == 409
        )
        c.cookies.clear()
        for url in [
            root + "/assets",
            root + "/assets/" + entry["id"],
            root + "/saves/" + save["id"],
            root + "/assets/" + entry["id"] + "/content",
            root + "/saves",
            root + "/saves/" + save["id"] + "/content",
        ]:
            assert c.get(url).status_code == 401


def test_save_management_without_editor_or_migration(tmp_path, monkeypatch):
    from vn_stream.engines.registry import ADAPTERS

    root = tmp_path / "games"
    source = root / "Source"
    (source / "game").mkdir(parents=True)
    (source / "game/script.rpy").write_text("label start: pass")
    app = create_app(tmp_path / "data", [root], "secret")
    with TestClient(app) as client:
        sign_in(client, "secret")
        project = api_data(client.post("/api/v2/projects", json={"path": str(source)}))
        project_id = project["project_id"]
        prefix = f"/api/v2/projects/{project_id}/saves"
        assert client.post(prefix + "/upload?name=slot.save", content=payload()).status_code == 200
        adapter = ADAPTERS["renpy"]
        monkeypatch.setattr(
            adapter,
            "capabilities",
            {**adapter.capabilities, "save_editing": False, "save_migration": False},
        )
        slot = api_data(client.get(prefix))["saves"][0]
        slot_url = f"{prefix}/{slot['id']}"
        assert client.get(slot_url).status_code == 200
        assert client.get(slot_url + "/content").status_code == 200
        response = client.get(slot_url + "/variables")
        variables = api_data(response)
        assert not variables["editable"] and variables["fields"] == [] and variables["revision"]
        assert response.json()["pagination"]["total"] == 0
        operations = (
            client.patch(
                slot_url + "/variables",
                json={
                    "revision": variables["revision"],
                    "changes": [{"id": "example", "type": "boolean", "value": True}],
                },
            ),
            client.get(prefix + "/signing"),
            client.get(prefix + "/migrations"),
        )
        for response in operations:
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "engine.unsupported"
