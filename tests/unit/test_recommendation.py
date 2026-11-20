from vn_stream.engines.renpy.launch import recommend_runtime


def test_exact_and_numeric_nearest_same_major():
    versions = ["8.10.0", "8.5.3", "8.3.7", "7.8.2"]
    assert recommend_runtime("8.3.7", versions) == {"version": "8.3.7", "kind": "exact"}
    assert recommend_runtime("8.5.2", versions) == {"version": "8.5.3", "kind": "newer"}
    assert recommend_runtime("8.6.0", versions)["version"] == "8.10.0"
    assert recommend_runtime("7.9.0", versions) == {"version": None, "kind": "unavailable"}
    assert recommend_runtime(None, versions)["version"] is None
    assert recommend_runtime("8.11.0", versions)["version"] is None
