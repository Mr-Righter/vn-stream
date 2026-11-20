"""Lifecycle ownership covers recovery and every cleanup even after failures."""

import threading
import time
from types import SimpleNamespace

import pytest

from vn_stream.application.bootstrap import Services
from vn_stream.assets.operations import Assets
from vn_stream.projects.catalog import ProjectCatalog


def test_assets_recover_only_when_explicitly_started(tmp_path):
    catalog = ProjectCatalog(tmp_path / "data", [tmp_path])
    assets = Assets(catalog, recover=False)
    assert not assets.threads
    assets.recover()
    assets.close()
    assert assets.stopping.is_set()


def test_asset_shutdown_drains_workers_and_prevents_new_work(tmp_path):
    assets = Assets(ProjectCatalog(tmp_path / "data", [tmp_path]), recover=False)
    finished = threading.Event()
    thread = threading.Thread(target=lambda: (time.sleep(0.03), finished.set()))
    assets.threads["owned"] = thread
    thread.start()
    assets.close()
    assert finished.is_set()
    assert not thread.is_alive()
    with pytest.raises(RuntimeError, match="closed"):
        assets._start("not-a-job")


def test_shutdown_failure_does_not_skip_other_resources():
    calls = []

    def close(name, fail=False):
        calls.append(name)
        if fail:
            raise RuntimeError(name)

    services = Services(
        None,
        SimpleNamespace(stop=lambda: close("session", True)),
        SimpleNamespace(close=lambda: close("imports")),
        SimpleNamespace(close=lambda: close("deletions", True)),
        SimpleNamespace(close=lambda: close("assets")),
        SimpleNamespace(
            signing_jobs=SimpleNamespace(close=lambda: close("signing")),
            migrations=SimpleNamespace(close=lambda: close("migrations")),
        ),
        None,
        None,
        SimpleNamespace(close=lambda: close("lease")),
    )
    with pytest.raises(ExceptionGroup) as error:
        services.close()
    assert len(error.value.exceptions) == 2
    assert calls == ["session", "imports", "deletions", "signing", "migrations", "assets", "lease"]
