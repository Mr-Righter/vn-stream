from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from vn_stream.streaming.health import expect_status


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.headers.get("Authorization") == "Bearer valid" else 401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_probe_distinguishes_protected_and_authenticated_endpoint(endpoint):
    expect_status(endpoint, 401)
    expect_status(endpoint, 200, {"Authorization": "Bearer valid"})


def test_probe_rejects_accidentally_open_endpoint(endpoint):
    with pytest.raises(RuntimeError, match="expected 401, received 200"):
        expect_status(endpoint, 401, {"Authorization": "Bearer valid"})
