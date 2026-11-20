"""Input-domain invariants for recommendation, paths and static save editing."""

import io
import zipfile

from hypothesis import given
from hypothesis import strategies as st

from tests.unit.test_save_codec import packed
from vn_stream.engines.renpy.launch import recommend_runtime
from vn_stream.saves.renpy import pickle_codec
from vn_stream.storage.files import safe

versions = st.tuples(st.integers(1, 12), st.integers(0, 30), st.integers(0, 30)).map(
    lambda parts: ".".join(map(str, parts))
)


@given(versions, st.lists(versions, max_size=30))
def test_runtime_recommendation_is_order_and_duplicate_independent(original, installed):
    result = recommend_runtime(original, installed)
    assert result == recommend_runtime(original, list(reversed(installed)) + installed)
    if original in installed:
        assert result == {"version": original, "kind": "exact"}
    elif result["version"] is not None:
        source = tuple(map(int, original.split(".")))
        selected = tuple(map(int, result["version"].split(".")))
        assert selected[0] == source[0]
        assert selected > source
        assert not any(
            source < tuple(map(int, value.split("."))) < selected
            for value in installed
            if value.split(".")[0] == original.split(".")[0]
        )


@given(st.lists(st.text(alphabet="abc中文123_-", min_size=1, max_size=12), min_size=1, max_size=12))
def test_project_path_preserves_components_without_escaping(parts):
    from pathlib import Path

    base = Path("/example/project")
    path = safe(base, "/".join(parts))
    assert path.is_relative_to(base)
    assert path.relative_to(base).parts == tuple(parts)


@given(st.integers(-(2**100), 2**100), st.integers(-(2**100), 2**100), st.sampled_from([2, 5]))
def test_integer_save_edit_preserves_unrelated_members(initial, replacement, protocol):
    original = packed({"store.counter": initial, "store.unrelated": "中文"}, protocol)
    field = next(
        f for f in pickle_codec.inspect(original)["fields"] if f["name"] == "store.counter"
    )
    modified = pickle_codec.edit(
        original, [{"id": field["id"], "type": field["type"], "value": str(replacement)}]
    )
    view = {f["name"]: f["value"] for f in pickle_codec.inspect(modified)["fields"]}
    assert view["store.counter"] == str(replacement)
    assert view["store.unrelated"] == "中文"
    with (
        zipfile.ZipFile(io.BytesIO(original)) as before,
        zipfile.ZipFile(io.BytesIO(modified)) as after,
    ):
        assert before.read("custom") == after.read("custom")
        assert before.read("json") == after.read("json")
