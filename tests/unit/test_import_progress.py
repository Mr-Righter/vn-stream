import io
import tarfile
import zipfile

from vn_stream.imports.archive import extract_archive


def test_zip_reports_actual_bytes_and_index_total(tmp_path):
    source = tmp_path / "sample.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("game/script.rpy", b"x" * (2 * 1024**2 + 7))
    updates = []
    extract_archive(
        source, tmp_path / "out", progress=lambda done, total: updates.append((done, total))
    )
    assert updates[0] == (0, 2 * 1024**2 + 7)
    assert updates[-1] == (2 * 1024**2 + 7, 2 * 1024**2 + 7)
    assert len(updates) >= 4
    assert updates == sorted(updates)


def test_tar_prepass_accepts_root_and_reports_total_before_writing(tmp_path):
    source = tmp_path / "sample.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        entry = tarfile.TarInfo("./game/script.rpy")
        entry.size = 3
        archive.addfile(entry, io.BytesIO(b"abc"))
    updates = []
    extract_archive(
        source, tmp_path / "out", progress=lambda done, total: updates.append((done, total))
    )
    assert updates == [(0, 3), (3, 3)]


def test_tar_prepass_validates_entire_archive_before_any_write(tmp_path):
    import pytest

    source = tmp_path / "unsafe.tar"
    with tarfile.open(source, "w") as archive:
        for name in ["game/script.rpy", "../escape"]:
            entry = tarfile.TarInfo(name)
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"abc"))
    updates = []
    with pytest.raises(ValueError):
        extract_archive(
            source, tmp_path / "out", progress=lambda done, total: updates.append((done, total))
        )
    assert updates == []
    assert not (tmp_path / "out").exists()


def test_progress_includes_7z_password_and_rar_actual_output(tmp_path):
    from pathlib import Path

    import py7zr

    source = tmp_path / "secret.7z"
    with py7zr.SevenZipFile(source, "w", password="secret", header_encryption=True) as archive:
        archive.writestr(b"compiled", "game/script.rpyc")
    for package, password, destination in [
        (source, "secret", tmp_path / "sevenzip"),
        (Path(__file__).parent / "archive_fixtures/rar5-psw.rar", "password", tmp_path / "rar"),
    ]:
        updates = []
        extract_archive(
            package,
            destination,
            password=password,
            progress=lambda done, total, updates=updates: updates.append((done, total)),
        )
        actual = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
        assert updates[0] == (0, actual)
        assert updates[-1] == (actual, actual)
        assert updates == sorted(updates)
