"""Curated official Linux SDKs; availability is not a compatibility claim."""

from dataclasses import dataclass
from pathlib import Path

from vn_stream.runtimes.catalog import available_runtimes


@dataclass(frozen=True)
class Release:
    version: str
    sha256: str

    @property
    def url(self) -> str:
        return f"https://www.renpy.org/dl/{self.version}/renpy-{self.version}-sdk.tar.bz2"

    @property
    def checksum_url(self) -> str:
        return f"https://www.renpy.org/dl/{self.version}/checksums.txt"


# SHA-256 entries in the official checksums.txt files, checked 2026-11-12.
# Keep the digest pinned: fetching a new digest during installation defeats the lock.
RELEASES = (
    Release("7.5.3", "6ec8c5beb93826b04afeb27b0cbc3e479672191a869bc3e4a1b88dd95cdd515a"),
    Release("7.8.2", "869a5a95dd05d8826c2540a8cc70bba4902404109215f62f5d033142ac2d695f"),
    Release("8.0.3", "7e91576c943342791bf2aceb067bb97991c063831b387536973a01f842543f25"),
    Release("8.1.3", "b15fdaf1322f18aaa1a588761f52543683aefd1a3c9e45c5bf045e3a4ad468b7"),
    Release("8.2.3", "0043c0110654d9c526ddf6419c800fc99ddf35dea8993ebf2126c4b2f16c90c2"),
    Release("8.3.2", "6d42b24e9c6db052a61b65cc20a121292b18b5a541532809795d3a3d3faf6d2d"),
    Release("8.3.7", "13a221c1e2ac6730e7d369fef93153b79940fa8ae3ec3660abd94c67dea58f4e"),
    Release("8.4.1", "7e398814f242b3c4e51b29b9edb7369d1c71434dacdeb2e83445f11addd55076"),
    Release("8.5.3", "eb0a9be7f0fb13632fe25ceade9a8bed5a1b4d6b6e83bd19eeeb29e1a1bb4a45"),
)


def describe_releases(root: Path, engines) -> list[dict]:
    installed = {
        runtime["version"]
        for runtime in available_runtimes(root, engines)
        if runtime["engine_id"] == "renpy"
    }
    return [
        {
            "version": release.version,
            "python": "2.7" if release.version.startswith("7.") else "3",
            "platform": "linux-x86_64",
            "url": release.url,
            "sha256": release.sha256,
            "checksum_url": release.checksum_url,
            "installed": release.version in installed,
        }
        for release in RELEASES
    ]
