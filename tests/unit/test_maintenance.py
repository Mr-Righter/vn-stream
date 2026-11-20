"""Maintenance checks use owned claim readers and never inspect foreign task tables."""

import pytest


def test_claims_respect_owner_and_single_active_session():
    from vn_stream.projects.maintenance import ProjectMaintenance

    guard = ProjectMaintenance()
    guard.register("signing", lambda project_id: ["sign-1"] if project_id == "a" else [])
    with pytest.raises(ValueError):
        guard.check("a")
    guard.check("a", signing_owner="sign-1")
    guard.check("b")
    guard.bind_session(lambda: {"state": "running", "project_id": "b"})
    with pytest.raises(ValueError):
        guard.check("b", include_session=True)
    guard.check("b")
