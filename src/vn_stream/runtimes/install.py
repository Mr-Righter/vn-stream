"""Install a pinned SDK without executing package code or overwriting an engine."""

import argparse
import fcntl
import hashlib
import json
import platform
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from vn_stream.engines.directories import engine_directory
from vn_stream.engines.registry import ADAPTERS
from vn_stream.engines.renpy.version_detection import detect_engine
from vn_stream.i18n import text
from vn_stream.runtimes.releases import RELEASES, Release, describe_releases


@dataclass(frozen=True)
class InstallLimits:
    compressed_bytes: int = 512 * 1024 * 1024
    expanded_bytes: int = 4 * 1024 * 1024 * 1024
    entries: int = 100_000
    seconds: int = 900


DEFAULT_LIMITS = InstallLimits()


def _copy_package(source, target: Path, release: Release, limits: InstallLimits):
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + limits.seconds
    with target.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > limits.compressed_bytes or time.monotonic() > deadline:
                raise ValueError(text("errors.theSdkDownloadExceededItsSizeOrTimeLimit"))
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != release.sha256:
        raise ValueError(text("errors.sdkSha256VerificationFailedNothingWasInstalled"))


def _extract(package: Path, destination: Path, version: str, limits: InstallLimits):
    prefix = f"renpy-{version}-sdk"
    total = 0
    with tarfile.open(package, mode="r|bz2") as archive:
        for count, member in enumerate(archive, 1):
            parts = member.name.rstrip("/").split("/")
            if (
                not parts
                or parts[0] != prefix
                or len(parts) > 32
                or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts)
            ):
                raise ValueError(text("errors.invalidSdkArchivePath"))
            total += member.size
            if count > limits.entries or total > limits.expanded_bytes:
                raise ValueError(text("errors.theSdkExceedsExtractionSizeOrFileLimits"))
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation also rejects duplicate files and file/dir conflicts.
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(text("errors.theSdkArchiveContainsLinksOrSpecialFiles"))
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(member.mode & 0o777)  # Never restore setuid, owners or devices.
            else:
                raise ValueError(text("errors.theSdkArchiveContainsLinksOrSpecialFiles"))
    sdk = destination / prefix
    interpreters = (
        sdk / "lib" / folder / "renpy"
        for folder in ("linux-x86_64", "py2-linux-x86_64", "py3-linux-x86_64")
    )
    if (
        not (sdk / "renpy.sh").is_file()
        or detect_engine(sdk, allow_logs=False).version != version
        or not any(p.is_file() and p.stat().st_mode & 0o111 for p in interpreters)
    ):
        raise ValueError(text("errors.theSdkEngineVersionOrLinuxX8664Runtime"))
    return sdk


def install_runtime(
    root: Path,
    release: Release,
    *,
    archive: Path | None = None,
    limits: InstallLimits = DEFAULT_LIMITS,
) -> Path:
    if platform.system() != "Linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise ValueError(text("errors.automaticInstallationSupportsLinuxX8664OnlyArmRuntimes"))
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    family = engine_directory(root, "renpy", ADAPTERS)
    family.mkdir(parents=True, exist_ok=True)
    # A shared file lease serializes API/CLI processes; game launches see only final dirs.
    with (root / ".install.lock").open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                text("errors.anotherSdkInstallationIsRunningPleaseRetryLater")
            ) from error
        target = family / release.version
        if target.exists() or target.is_symlink():
            raise ValueError(text("runtime.folderExists", path=str(target)))
        with TemporaryDirectory(prefix=".install-", dir=family) as temporary:
            stage = Path(temporary)
            package = stage / "sdk.tar.bz2"
            if archive is None:
                with urlopen(release.url, timeout=30) as source:
                    _copy_package(source, package, release, limits)
            else:
                with archive.open("rb") as source:
                    _copy_package(source, package, release, limits)
            sdk = _extract(package, stage / "unpacked", release.version, limits)
            (sdk / ".vn-stream-runtime.json").write_text(
                json.dumps(
                    {"version": release.version, "url": release.url, "sha256": release.sha256}
                )
            )
            sdk.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=text("errors.installAPinnedOfficialLinuxRenPySdk"))
    parser.add_argument("version", nargs="?", choices=[release.version for release in RELEASES])
    parser.add_argument("--data", type=Path, default=Path("var"))
    parser.add_argument(
        "--archive", type=Path, help=text("errors.offlineOfficialTarBz2PackageThePinnedSha256")
    )
    parser.add_argument(
        "--list", action="store_true", help=text("errors.showTargetVersionsAndInstallationStatus")
    )
    args = parser.parse_args()
    if args.list:
        print(
            json.dumps(
                describe_releases(args.data / "runtimes", ADAPTERS), ensure_ascii=False, indent=2
            )
        )
        return
    if args.version is None:
        parser.error(text("errors.specifyAVersionOrList"))
    release = next(release for release in RELEASES if release.version == args.version)
    try:
        installed = install_runtime(args.data / "runtimes", release, archive=args.archive)
    except (ValueError, OSError, tarfile.TarError, EOFError):
        parser.exit(1, str(text("runtime.installFailed")) + "\n")
    print(text("runtime.installed", version=release.version) + ": " + str(installed))


if __name__ == "__main__":
    main()
