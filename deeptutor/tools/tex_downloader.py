"""
TeX Downloader - LaTeX source code download tool

Features:
1. Download LaTeX source from ArXiv
2. Extract and locate main tex file
3. Read tex content

Author: DeepTutor Team
Version: v1.0
Based on: TODO.md specification
"""

import logging
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import zipfile

import requests

logger = logging.getLogger(__name__)

_ARXIV_ID_PATTERN = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")
_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 200.0
_COPY_CHUNK_BYTES = 64 * 1024


class TexDownloadResult:
    """LaTeX download result"""

    def __init__(
        self,
        success: bool,
        tex_path: str | None = None,
        tex_content: str | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.tex_path = tex_path
        self.tex_content = tex_content
        self.error = error


class TexDownloader:
    """LaTeX source code download tool"""

    def __init__(self, workspace_dir: str):
        """
        Initialize downloader

        Args:
            workspace_dir: Workspace directory (for saving downloaded files)
        """
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def download_arxiv_source(
        self, arxiv_url: str, arxiv_id: str | None = None
    ) -> TexDownloadResult:
        """
        Download LaTeX source from ArXiv

        Args:
            arxiv_url: ArXiv paper URL
            arxiv_id: ArXiv ID (optional, if not in URL)

        Returns:
            TexDownloadResult object
        """
        # Extract ArXiv ID
        if not arxiv_id:
            arxiv_id = self._extract_arxiv_id(arxiv_url)

        if not arxiv_id or not _ARXIV_ID_PATTERN.fullmatch(arxiv_id):
            return TexDownloadResult(success=False, error="Unable to extract ArXiv ID")

        temp_dir: str | None = None
        try:
            # Build source download URL
            source_url = f"https://arxiv.org/e-print/{arxiv_id}"

            # Download source package
            print(f"  Downloading source: {source_url}")
            response = requests.get(source_url, timeout=30)
            response.raise_for_status()

            # Create temporary directory
            temp_dir = tempfile.mkdtemp(dir=self.workspace_dir)

            # Save source package
            source_file = Path(temp_dir) / f"{arxiv_id}_source"
            with open(source_file, "wb") as f:
                f.write(response.content)

            # Extract source package
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir(exist_ok=True)

            if self._is_tar_file(source_file):
                self._extract_tar(source_file, extract_dir)
            elif self._is_zip_file(source_file):
                self._extract_zip(source_file, extract_dir)
            else:
                # Might be a single tex file
                shutil.copy(source_file, extract_dir / f"{arxiv_id}.tex")

            # Find main tex file
            main_tex = self._find_main_tex(extract_dir)

            if not main_tex:
                return TexDownloadResult(success=False, error="Main tex file not found")

            # Read tex content
            tex_content = self._read_tex_file(main_tex)

            # Move to permanent location
            paper_dir = self.workspace_dir / f"paper_{arxiv_id}"
            paper_dir.mkdir(exist_ok=True)

            final_tex_path = paper_dir / "main.tex"
            shutil.copy(main_tex, final_tex_path)

            return TexDownloadResult(
                success=True, tex_path=str(final_tex_path), tex_content=tex_content
            )

        except requests.exceptions.RequestException as e:
            return TexDownloadResult(success=False, error=f"Download failed: {e!s}")
        except Exception as e:
            return TexDownloadResult(success=False, error=f"Processing failed: {e!s}")
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _extract_arxiv_id(self, url: str) -> str | None:
        """Extract ArXiv ID from URL"""
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", url)
        if match:
            return match.group(1)
        return None

    def _is_tar_file(self, file_path: Path) -> bool:
        """Check if file is a tar file"""
        try:
            with tarfile.open(file_path, "r:*") as tar:
                return True
        except Exception:
            return False

    def _is_zip_file(self, file_path: Path) -> bool:
        """Check if file is a zip file"""
        try:
            with zipfile.ZipFile(file_path, "r") as zip_file:
                return True
        except Exception:
            return False

    def _extract_tar(self, tar_path: Path, extract_dir: Path):
        """Extract an arXiv tarball without allowing links or path escapes."""
        with tarfile.open(tar_path, "r:*") as tar:
            members = tar.getmembers()
            self._validate_archive_entry_count(len(members))

            total_bytes = 0
            safe_members: list[tarfile.TarInfo] = []
            for member in members:
                if member.issym() or member.islnk():
                    raise ValueError(f"Archive links are not allowed: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"Unsupported tar member type: {member.name}")
                if member.isfile():
                    total_bytes = self._checked_archive_size(member.name, member.size, total_bytes)
                try:
                    filtered = tarfile.data_filter(member, extract_dir)
                except tarfile.FilterError as exc:
                    raise ValueError(f"Unsafe tar member: {member.name}") from exc
                if filtered is not None:
                    safe_members.append(filtered)

            tar.extractall(extract_dir, members=safe_members, filter="data")

    def _extract_zip(self, zip_path: Path, extract_dir: Path):
        """Extract an arXiv ZIP while preserving its safe relative tree."""
        target_root = extract_dir.resolve()
        with zipfile.ZipFile(zip_path, "r") as archive:
            members = archive.infolist()
            self._validate_archive_entry_count(len(members))

            total_bytes = 0
            destinations: list[tuple[zipfile.ZipInfo, Path]] = []
            for member in members:
                destination = self._zip_destination(member, target_root)
                if member.is_dir():
                    destinations.append((member, destination))
                    continue
                total_bytes = self._checked_archive_size(
                    member.filename, member.file_size, total_bytes
                )
                if member.flag_bits & 0x1:
                    raise ValueError(f"Encrypted zip member is not allowed: {member.filename}")
                if member.file_size:
                    if member.compress_size <= 0:
                        raise ValueError(
                            f"Invalid compressed size for zip member: {member.filename}"
                        )
                    ratio = member.file_size / member.compress_size
                    if ratio > _MAX_ZIP_COMPRESSION_RATIO:
                        raise ValueError(
                            f"Suspicious compression ratio for zip member: {member.filename}"
                        )
                destinations.append((member, destination))

            written_total = 0
            for member, destination in destinations:
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with archive.open(member) as source, open(destination, "wb") as sink:
                    while chunk := source.read(_COPY_CHUNK_BYTES):
                        written += len(chunk)
                        written_total += len(chunk)
                        if (
                            written > member.file_size
                            or written > _MAX_ARCHIVE_MEMBER_BYTES
                            or written_total > _MAX_ARCHIVE_TOTAL_BYTES
                        ):
                            raise ValueError(
                                f"Zip member exceeds its declared or configured size: "
                                f"{member.filename}"
                            )
                        sink.write(chunk)

    @staticmethod
    def _validate_archive_entry_count(count: int) -> None:
        if count > _MAX_ARCHIVE_ENTRIES:
            raise ValueError(f"Archive has too many entries: {count} > {_MAX_ARCHIVE_ENTRIES}")

    @staticmethod
    def _checked_archive_size(name: str, size: int, current_total: int) -> int:
        if size < 0 or size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError(f"Archive member is too large: {name}")
        total = current_total + size
        if total > _MAX_ARCHIVE_TOTAL_BYTES:
            raise ValueError("Archive exceeds the total extraction size limit")
        return total

    @staticmethod
    def _zip_destination(member: zipfile.ZipInfo, target_root: Path) -> Path:
        normalized = member.filename.replace("\\", "/")
        relative = Path(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe zip member path: {member.filename}")

        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"Unsupported zip member type: {member.filename}")

        destination = (target_root / relative).resolve()
        try:
            destination.relative_to(target_root)
        except ValueError as exc:
            raise ValueError(f"Unsafe zip member path: {member.filename}") from exc
        return destination

    def _find_main_tex(self, directory: Path) -> Path | None:
        """
        Find main tex file

        Priority:
        1. main.tex
        2. paper.tex
        3. Tex file containing \\documentclass
        4. Largest tex file
        """
        tex_files = list(directory.rglob("*.tex"))

        if not tex_files:
            return None

        # 1. Find main.tex or paper.tex
        for name in ["main.tex", "paper.tex", "manuscript.tex"]:
            for tex_file in tex_files:
                if tex_file.name.lower() == name:
                    return tex_file

        # 2. Find file containing \documentclass
        for tex_file in tex_files:
            try:
                content = tex_file.read_text(encoding="utf-8", errors="ignore")
                if r"\documentclass" in content:
                    return tex_file
            except Exception:
                logger.warning("Failed to read tex file %s", tex_file)
                continue

        # 3. Return largest tex file
        largest_tex = max(tex_files, key=lambda f: f.stat().st_size)
        return largest_tex

    def _read_tex_file(self, tex_path: Path) -> str:
        """Read tex file content"""
        try:
            return tex_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            raise Exception(f"Failed to read tex file: {e!s}")


def read_tex_file(tex_path: str) -> str:
    """
    Read tex file content (convenience function)

    Args:
        tex_path: tex file path

    Returns:
        tex content
    """
    return Path(tex_path).read_text(encoding="utf-8", errors="ignore")


# ========== Usage Example ==========

if __name__ == "__main__":
    # Test download
    downloader = TexDownloader(workspace_dir="./test_workspace")

    # Test an ArXiv paper
    result = downloader.download_arxiv_source(
        arxiv_url="https://arxiv.org/abs/1706.03762",  # Attention is All You Need
        arxiv_id="1706.03762",
    )

    if result.success:
        print("✓ Download successful!")
        print(f"  File path: {result.tex_path}")
        print(f"  Content length: {len(result.tex_content)} characters")
        print(f"  Content preview: {result.tex_content[:500]}...")
    else:
        print(f"✗ Download failed: {result.error}")
