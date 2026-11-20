import io
import pickle
import zipfile

import pytest

from vn_stream.saves.renpy import pickle_codec as save_codec


def packed(roots, protocol=2):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("log", pickle.dumps((roots, {"untouched": [1, 2]}), protocol))
        z.writestr("json", '{"_version":"1.0"}')
        z.writestr("custom", b"keep")
    return out.getvalue()


@pytest.mark.parametrize("protocol", [2, 5])
def test_primitive_edits_preserve_graph_and_archive(protocol):
    data = packed(
        {
            "store.flag": False,
            "store.name": "中文",
            "store.money": 2**65,
            "store.ratio": 1.5,
            "store.object": {"nested": True},
            "store._internal": 4,
        },
        protocol,
    )
    view = save_codec.inspect(data)
    assert len(view["fields"]) == 4
    changes = [
        {
            "id": f["id"],
            "type": f["type"],
            "value": "changed" if f["type"] == "text" else True if f["type"] == "boolean" else "42",
        }
        for f in view["fields"]
    ]
    result = save_codec.edit(data, changes)
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        roots, log = pickle.loads(z.read("log"))  # Only self-authored trusted fixtures are loaded.
        assert roots["store.flag"] is True
        assert roots["store.name"] == "changed"
        assert roots["store.money"] == 42
        assert roots["store.ratio"] == 42.0
        assert roots["store.object"] == {"nested": True}
        assert roots["store._internal"] == 4
        assert log == {"untouched": [1, 2]}
        assert z.read("custom") == b"keep"


def test_no_game_reducers_are_executed(tmp_path):
    marker = tmp_path / "never"

    class Executable:
        def __reduce__(self):
            return (eval, (f"open({str(marker)!r},'w').write('bad')",))

    data = packed({"store.opaque": Executable(), "store.flag": False})
    view = save_codec.inspect(data)
    assert [f["name"] for f in view["fields"]] == ["store.flag"]
    save_codec.edit(data, [dict(view["fields"][0], value=True)])
    assert not marker.exists()


def test_invalid_type_and_unknown_field_rejected():
    data = packed({"store.money": 1})
    with pytest.raises(ValueError):
        save_codec.edit(data, [{"id": "missing", "type": "text", "value": "x"}])
    field = save_codec.inspect(data)["fields"][0]
    with pytest.raises(ValueError):
        save_codec.edit(data, [dict(field, type="text", value="x")])
    with pytest.raises(ValueError):
        save_codec.edit(data, [dict(field, value="NaN")])
