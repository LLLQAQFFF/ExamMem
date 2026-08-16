from __future__ import annotations

from io import BytesIO
from pathlib import Path
import stat
import tarfile
import zipfile

import pytest

from deeptutor.tools.tex_downloader import TexDownloader


def _tar(path: Path, entries: list[tuple[tarfile.TarInfo, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for info, content in entries:
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
    return path


def _zip_bytes(entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> bytes:
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, content in entries:
            archive.writestr(info, content)
    return payload.getvalue()


def test_extract_tar_preserves_safe_source_tree(tmp_path: Path) -> None:
    source = _tar(
        tmp_path / "source.tar.gz",
        [(tarfile.TarInfo("paper/sections/main.tex"), b"\\documentclass{article}")],
    )
    destination = tmp_path / "out"
    destination.mkdir()

    TexDownloader(str(tmp_path))._extract_tar(source, destination)

    assert (destination / "paper/sections/main.tex").read_bytes() == b"\\documentclass{article}"


def test_extract_tar_rejects_path_traversal(tmp_path: Path) -> None:
    source = _tar(
        tmp_path / "evil.tar.gz",
        [(tarfile.TarInfo("../../escape.tex"), b"escaped")],
    )
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ValueError, match="Unsafe tar member"):
        TexDownloader(str(tmp_path))._extract_tar(source, destination)

    assert not (tmp_path / "escape.tex").exists()


def test_extract_tar_rejects_links(tmp_path: Path) -> None:
    link = tarfile.TarInfo("paper/main.tex")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../escape.tex"
    source = _tar(tmp_path / "link.tar.gz", [(link, b"")])
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ValueError, match="Archive links are not allowed"):
        TexDownloader(str(tmp_path))._extract_tar(source, destination)


def test_extract_zip_preserves_safe_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(_zip_bytes([("paper/sections/main.tex", b"\\documentclass{article}")]))
    destination = tmp_path / "out"
    destination.mkdir()

    TexDownloader(str(tmp_path))._extract_zip(source, destination)

    assert (destination / "paper/sections/main.tex").read_bytes() == b"\\documentclass{article}"


def test_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    source = tmp_path / "evil.zip"
    source.write_bytes(_zip_bytes([("../../escape.tex", b"escaped")]))
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ValueError, match="Unsafe zip member path"):
        TexDownloader(str(tmp_path))._extract_zip(source, destination)

    assert not (tmp_path / "escape.tex").exists()


def test_extract_zip_rejects_links(tmp_path: Path) -> None:
    link = zipfile.ZipInfo("paper/main.tex")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    source = tmp_path / "link.zip"
    source.write_bytes(_zip_bytes([(link, b"../../escape.tex")]))
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ValueError, match="Unsupported zip member type"):
        TexDownloader(str(tmp_path))._extract_zip(source, destination)


def test_invalid_explicit_arxiv_id_is_rejected_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def unexpected_request(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("request must not run")

    monkeypatch.setattr("deeptutor.tools.tex_downloader.requests.get", unexpected_request)

    result = TexDownloader(str(tmp_path)).download_arxiv_source(
        "https://arxiv.org/abs/1706.03762", "../../outside"
    )

    assert not result.success
    assert result.error == "Unable to extract ArXiv ID"
    assert not called


def test_failed_archive_processing_removes_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _zip_bytes([("../../escape.tex", b"escaped")])

    class Response:
        content = payload

        @staticmethod
        def raise_for_status() -> None:
            return None

    monkeypatch.setattr(
        "deeptutor.tools.tex_downloader.requests.get",
        lambda *_args, **_kwargs: Response(),
    )

    result = TexDownloader(str(tmp_path)).download_arxiv_source("https://arxiv.org/abs/1706.03762")

    assert not result.success
    assert "Unsafe zip member path" in (result.error or "")
    assert list(tmp_path.iterdir()) == []
