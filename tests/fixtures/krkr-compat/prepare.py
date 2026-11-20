"""Generate redistributable regressions for native renderer and PSB contracts."""

import argparse
import struct
from pathlib import Path


def empty_psb():
    """A PSB3 empty dictionary with valid zero-width, zero-count arrays."""
    array = b"\x0d\x00\x0c"
    # Names contain charset, trie and key-index arrays. No strings or chunks.
    return (
        b"PSB\0" + struct.pack("<HH9I", 3, 0, 44, 44, 53, 56, 56, 59, 62, 62, 0)
        + array * 6 + b"\x21" + array * 2
    )


def prepare(target, mode):
    target.mkdir(parents=True, exist_ok=True)
    if mode == "psb":
        good = empty_psb()
        (target / "empty.psb").write_bytes(good)
        broken = bytearray(good)
        broken[57] = 255
        (target / "truncated.psb").write_bytes(broken)
        script = '''Plugins.link("psbfile.dll");
var psb = new PSBFile();
if (!psb.load("empty.psb")) throw "valid empty PSB rejected";
if (psb.load("truncated.psb")) throw "truncated PSB accepted";
Debug.message("VN_COMPAT_PSB_OK");
'''
    else:
        renderer = "opengl" if mode == "opengl" else "missing-test-renderer"
        (target / "Kirikiroid2Preference.xml").write_text(
            f'<GlobalPreference><Item key="renderer" value="{renderer}"/></GlobalPreference>'
        )
        script = '''global.win = new Window();
win.setSize(320, 240);
try {
    global.layer = new Layer(win, null);
    layer.setSize(320, 240);
    layer.setImageSize(320, 240);
    layer.fillRect(0, 0, 320, 240, 0xff2060c0);
    layer.visible = true;
    win.visible = true;
    Debug.message("VN_COMPAT_RENDERER_OK");
} catch (error) {
    Debug.message("VN_COMPAT_RENDERER_ERROR:" + error.message);
}
'''
    (target / "startup.tjs").write_text(script, encoding="utf-16")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("mode", choices=("psb", "opengl", "unknown"))
    args = parser.parse_args()
    prepare(args.target, args.mode)
