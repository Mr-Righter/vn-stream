"""All archives here are tiny self-authored fixtures generated in each test."""

import io
import tarfile

import py7zr
import pytest
import pyzipper

from vn_stream.imports.archive import Limits, extract_archive
from vn_stream.imports.publication import import_project


@pytest.mark.parametrize(
    "suffix,mode", [(".tar", "w"), (".tar.gz", "w:gz"), (".tar.bz2", "w:bz2"), (".tar.xz", "w:xz")]
)
def test_tar_formats_preserve_content(tmp_path, suffix, mode):
    source = tmp_path / ("sample" + suffix)
    with tarfile.open(source, mode) as archive:
        entry = tarfile.TarInfo("Project/game/script.rpyc")
        entry.size = 8
        archive.addfile(entry, io.BytesIO(b"compiled"))
    extract_archive(source, tmp_path / "out")
    assert (tmp_path / "out/Project/game/script.rpyc").read_bytes() == b"compiled"


@pytest.mark.parametrize("kind", ["zip", "7z"])
@pytest.mark.parametrize("password", [None, "secret"])
def test_encrypted_formats(tmp_path, kind, password):
    source = tmp_path / ("sample." + kind)
    if kind == "zip":
        with pyzipper.AESZipFile(source, "w", encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword(b"secret")
            archive.writestr("game/script.rpyc", b"compiled")
    else:
        with py7zr.SevenZipFile(source, "w", password="secret", header_encryption=True) as archive:
            archive.writestr(b"compiled", "game/script.rpyc")
    if password:
        extract_archive(source, tmp_path / "out", password=password)
        assert (tmp_path / "out/game/script.rpyc").read_bytes() == b"compiled"
    else:
        with pytest.raises(ValueError):
            extract_archive(source, tmp_path / "out", password=password)
        assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["tar", "7z"])
@pytest.mark.parametrize("name", ["../outside", "/outside", "C:/outside", "a\\..\\..\\outside"])
def test_formats_reject_unsafe_names(tmp_path, kind, name):
    source = tmp_path / ("bad." + kind)
    if kind == "tar":
        with tarfile.open(source, "w") as archive:
            entry = tarfile.TarInfo(name)
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
    else:
        # py7zr's public writer refuses absolute/traversal names; patch only fixture metadata.
        with py7zr.SevenZipFile(source, "w") as archive:
            archive.writestr(b"x", "safe")
            archive.header.files_info.files[0]["filename"] = name
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["tar", "7z"])
@pytest.mark.parametrize("limits", [Limits(max_bytes=1), Limits(max_files=0), Limits(max_depth=1)])
def test_formats_enforce_limits(tmp_path, kind, limits):
    source = tmp_path / ("sample." + kind)
    if kind == "tar":
        with tarfile.open(source, "w") as archive:
            entry = tarfile.TarInfo("game/script.rpyc")
            entry.size = 8
            archive.addfile(entry, io.BytesIO(b"compiled"))
    else:
        with py7zr.SevenZipFile(source, "w") as archive:
            archive.writestr(b"compiled", "game/script.rpyc")
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out", limits)
    assert not (tmp_path / "out").exists()


def test_unknown_extension_is_rejected(tmp_path):
    source = tmp_path / "file.unknown"
    with pyzipper.AESZipFile(source, "w") as archive:
        archive.writestr("game/script.rpyc", b"compiled")
    with pytest.raises(ValueError, match="格式"):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("type_", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
@pytest.mark.parametrize("name", ["game/link", "."])
def test_tar_rejects_links_and_special_files(tmp_path, type_, name):
    source = tmp_path / "bad.tar"
    with tarfile.open(source, "w") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = type_
        entry.linkname = "../outside"
        archive.addfile(entry)
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "fixture,password",
    [("rar5-crc.rar", None), ("rar5-psw.rar", "password"), ("rar5-hpsw.rar", "password")],
)
def test_rar_real_fixtures(tmp_path, fixture, password):
    import hashlib
    from pathlib import Path

    source = Path(__file__).parent / "archive_fixtures" / fixture
    extract_archive(source, tmp_path / "out", password=password)
    first = (tmp_path / "out/stest1.txt").read_bytes()
    second = (tmp_path / "out/stest2.txt").read_bytes()
    assert len(first) == 2048
    assert first == second
    assert (
        hashlib.sha256(first).hexdigest()
        == "2eaebb4c18cdef7f20089f8a2fa3475bc59c2a193f66e2f1513609a4bef13e22"
    )


@pytest.mark.parametrize("fixture", ["rar5-psw.rar", "rar5-hpsw.rar"])
@pytest.mark.parametrize("password", [None, "wrong-secret"])
def test_rar_bad_password_cleans_up(tmp_path, fixture, password):
    from pathlib import Path

    source = Path(__file__).parent / "archive_fixtures" / fixture
    with pytest.raises(ValueError) as error:
        extract_archive(source, tmp_path / "out", password=password)
    assert "wrong-secret" not in str(error.value)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kind", ["tar", "7z"])
def test_duplicate_paths_are_rejected(tmp_path, kind):
    source = tmp_path / ("bad." + kind)
    if kind == "tar":
        with tarfile.open(source, "w") as archive:
            for _ in range(2):
                entry = tarfile.TarInfo("game/script.rpyc")
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
    else:
        with py7zr.SevenZipFile(source, "w") as archive:
            archive.writestr(b"x", "game/script.rpyc")
            archive.writestr(b"y", "game/script.rpyc")
    with pytest.raises(ValueError, match="重复"):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_actual_writes_are_bounded_even_with_wrong_metadata(tmp_path, monkeypatch):
    source = tmp_path / "bad.tar"
    with tarfile.open(source, "w") as archive:
        entry = tarfile.TarInfo("game/script.rpyc")
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    # Model a decoder returning more than its declared size; no unbounded copy is allowed.
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda *_: io.BytesIO(b"xx"))
    with pytest.raises(ValueError, match="实际展开"):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("password", ["wrong-secret"])
@pytest.mark.parametrize("kind", ["zip", "7z"])
def test_encrypted_wrong_password_cleans_up(tmp_path, kind, password):
    source = tmp_path / ("sample." + kind)
    if kind == "zip":
        with pyzipper.AESZipFile(source, "w", encryption=pyzipper.WZ_AES) as archive:
            archive.setpassword(b"secret")
            archive.writestr("game/script.rpyc", b"compiled")
    else:
        with py7zr.SevenZipFile(source, "w", password="secret") as archive:
            archive.writestr(b"compiled", "game/script.rpyc")
    with pytest.raises(ValueError) as error:
        extract_archive(source, tmp_path / "out", password=password)
    assert password not in str(error.value)
    assert not (tmp_path / "out").exists()


def test_sevenzip_preserves_empty_files_and_directories(tmp_path):
    source = tmp_path / "sample.7z"
    empty = tmp_path / "empty"
    empty.mkdir()
    with py7zr.SevenZipFile(source, "w") as archive:
        archive.writestr(b"compiled", "game/script.rpyc")
        archive.writestr(b"", "game/empty")
        archive.write(empty, "game/images")
    extract_archive(source, tmp_path / "out")
    assert (tmp_path / "out/game/script.rpyc").read_bytes() == b"compiled"
    assert (tmp_path / "out/game/empty").read_bytes() == b""
    assert (tmp_path / "out/game/images").is_dir()


def test_sevenzip_rejects_symlinks(tmp_path):
    source = tmp_path / "bad.7z"
    link = tmp_path / "link"
    (tmp_path / "outside").write_bytes(b"outside")
    link.symlink_to("outside")
    with py7zr.SevenZipFile(source, "w") as archive:
        archive.write(link, "game/link")
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_preexisting_destination_is_never_deleted(tmp_path):
    source = tmp_path / "sample.tar"
    source.write_bytes(b"bad")
    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "original").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        extract_archive(source, destination)
    assert (destination / "original").read_bytes() == b"keep"


@pytest.mark.parametrize("names", [("game", "game/script.rpyc"), ("game/script.rpyc", "game")])
def test_tar_file_directory_conflicts_are_rejected(tmp_path, names):
    source = tmp_path / "bad.tar"
    with tarfile.open(source, "w") as archive:
        for name in names:
            entry = tarfile.TarInfo(name)
            entry.size = 1
            archive.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("password", ["secret", None, "incorrect"])
def test_zipcrypto_passwords(tmp_path, password):
    from pathlib import Path

    source = Path(__file__).parent / "archive_fixtures/zipcrypto.zip"
    if password == "secret":
        extract_archive(source, tmp_path / "out", password=password)
        assert (tmp_path / "out/game/script.rpyc").read_bytes() == b"compiled"
    else:
        with pytest.raises(ValueError):
            extract_archive(source, tmp_path / "out", password=password)
        assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("limits", [Limits(max_bytes=10), Limits(max_files=1)])
def test_rar_limits(tmp_path, limits):
    from pathlib import Path

    source = Path(__file__).parent / "archive_fixtures/rar5-crc.rar"
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out", limits)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "name",
    [
        "game.ZIP",
        "game.APK",
        "game.7Z",
        "game.RAR",
        "game.TAR",
        "game.TAR.GZ",
        "game.TAR.BZ2",
        "game.TAR.XZ",
    ],
)
def test_supported_extension_detection(name):
    from vn_stream.imports.archive import supports_archive

    assert supports_archive(name)


@pytest.mark.parametrize("name", ["game.gz", "game.exe", "game.zip.exe", "game.ipa"])
def test_unsupported_extension_detection(name):
    from vn_stream.imports.archive import supports_archive

    assert not supports_archive(name)


@pytest.mark.parametrize(
    "suffix,mode", [(".tar", "w"), (".tar.gz", "w:gz"), (".tar.bz2", "w:bz2"), (".tar.xz", "w:xz")]
)
def test_tar_current_root_marker_imports_complete_project(tmp_path, suffix, mode):
    from pathlib import Path

    from vn_stream.projects.catalog import ProjectCatalog

    project = tmp_path / "Story"
    (project / "game").mkdir(parents=True)
    (project / "game/script.rpy").write_bytes(b"label start:\n    return\n")
    source = tmp_path / ("Story" + suffix)
    with tarfile.open(source, mode) as archive:
        archive.add(project, arcname=".")
    with tarfile.open(source) as archive:
        assert archive.getnames() == [".", "./game", "./game/script.rpy"]
    extract_archive(source, tmp_path / "out")
    assert (tmp_path / "out/game/script.rpy").read_bytes() == b"label start:\n    return\n"
    library = ProjectCatalog(tmp_path / "data", [tmp_path])
    game = import_project(library, source)
    assert (Path(game.path) / "game/script.rpy").read_bytes() == b"label start:\n    return\n"
    assert library.get(game.project_id) == game


@pytest.mark.parametrize(
    "name,directory,size",
    [
        (".", False, 0),
        ("./", False, 1),
        (".", True, 1),
        ("/", True, 0),
        ("../", True, 0),
        (".\\", True, 0),
        ("./../", True, 0),
    ],
)
def test_tar_rejects_invalid_current_root_marker(tmp_path, name, directory, size):
    source = tmp_path / "bad.tar"
    with tarfile.open(source, "w") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
        entry.size = size
        archive.addfile(entry, io.BytesIO(b"x" * size))
    with pytest.raises(ValueError):
        extract_archive(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "names,limits,diagnostic",
    [
        (["."], Limits(max_files=0), "数量"),
        ([".", "./game/script.rpy"], Limits(max_files=1), "数量"),
        ([".", "./game/script.rpy"], Limits(max_bytes=0), "大小"),
        ([".", "./"], Limits(), "重复"),
    ],
)
def test_tar_root_marker_keeps_member_limits_and_duplicates(tmp_path, names, limits, diagnostic):
    source = tmp_path / "bad.tar"
    with tarfile.open(source, "w") as archive:
        for name in names:
            entry = tarfile.TarInfo(name)
            directory = name in (".", "./")
            entry.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
            entry.size = 0 if directory else 1
            archive.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match=diagnostic):
        extract_archive(source, tmp_path / "out", limits)
    assert not (tmp_path / "out").exists()
