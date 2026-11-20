from fastapi.testclient import TestClient as BaseClient


class TestClient(BaseClient):
    __test__ = False

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("base_url", "https://testserver")
        super().__init__(*args, **kwargs)


def sign_in(client, password, username="player"):
    response = client.post("/api/v2/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = api_data(response)["csrf_token"]


def api_data(response):
    """Read the v1 response payload while keeping failure envelopes inspectable."""
    body = response.json()
    return body["data"] if isinstance(body, dict) and "data" in body else body
