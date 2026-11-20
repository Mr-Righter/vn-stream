"""Generate sprite strips with known frame intervals for native timing probes."""

import argparse
from pathlib import Path

from PIL import Image


def prepare(target):
    target.mkdir(parents=True, exist_ok=True)
    strip = Image.new("RGB", (320, 32))
    for frame in range(10):
        strip.paste(((frame + 1) * 20, 40, 100), (frame * 32, 0, (frame + 1) * 32, 32))
    strip.save(target / "strip.png")
    script = [';mode320', '*define', 'caption "VN ONS Timing"', 'game', '*start', 'bg black,1']
    for row, duration in enumerate((20, 30, 50, 100)):
        script.append(f'lsp {row},":c/10,{duration},0;strip.png",0,{row * 40}')
    script.extend(['print 1', 'wait 15000', 'end'])
    (target / "0.txt").write_text("\n".join(script) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    prepare(parser.parse_args().target)
