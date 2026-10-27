"""Install the pinned Stockfish release used by chess-lens-mcp."""

import hashlib
import os
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import BinaryIO

RELEASE_URL = (
    "https://github.com/official-stockfish/Stockfish/releases/download/"
    "sf_16/stockfish-ubuntu-x86-64.tar"
)
RELEASE_SHA256 = "efca1c60ec11fd9628425f3ee40644ad1618535ddf881c16385a86f7fc9e0983"
INSTALL_DIR = Path("/opt/stockfish-16.0")
EXECUTABLE = INSTALL_DIR / "stockfish"
EXECUTABLE_LINK = Path("/usr/games/stockfish")
ARCHIVE_EXECUTABLE = "stockfish/stockfish-ubuntu-x86-64"
ARCHIVE_LICENSE = "stockfish/Copying.txt"


def download_release(destination: Path) -> None:
    digest = hashlib.sha256()
    request = urllib.request.Request(
        RELEASE_URL, headers={"User-Agent": "chess-lens-mcp-installer"}
    )
    with (
        urllib.request.urlopen(request, timeout=120) as response,
        destination.open("wb") as archive_file,
    ):
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            archive_file.write(chunk)

    actual_digest = digest.hexdigest()
    if actual_digest != RELEASE_SHA256:
        raise RuntimeError(
            "Stockfish release checksum mismatch: "
            f"expected {RELEASE_SHA256}, got {actual_digest}"
        )


def copy_archive_member(
    archive: tarfile.TarFile, member_name: str, destination: Path
) -> None:
    try:
        member = archive.getmember(member_name)
    except KeyError as error:
        raise RuntimeError(
            f"Stockfish release archive is missing {member_name}"
        ) from error
    if not member.isfile():
        raise RuntimeError(
            f"Stockfish release entry is not a regular file: {member_name}"
        )

    source: BinaryIO | None = archive.extractfile(member)
    if source is None:
        raise RuntimeError(f"Unable to read Stockfish release entry: {member_name}")
    with source, destination.open("wb") as destination_file:
        shutil.copyfileobj(source, destination_file)


def main() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise SystemExit("The pinned Stockfish asset supports x86-64 Linux only")
    if os.geteuid() != 0:
        raise SystemExit("Run this installer as root to write /opt and /usr/games")

    with tempfile.TemporaryDirectory(prefix="chess-lens-stockfish-") as temp_dir:
        archive_path = Path(temp_dir) / "stockfish.tar"
        download_release(archive_path)

        INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".stockfish-16.0-", dir=INSTALL_DIR.parent
        ) as staging_dir:
            staging_path = Path(staging_dir)
            with tarfile.open(archive_path, mode="r:") as archive:
                staged_executable = staging_path / "stockfish"
                copy_archive_member(archive, ARCHIVE_EXECUTABLE, staged_executable)
                copy_archive_member(
                    archive, ARCHIVE_LICENSE, staging_path / "Copying.txt"
                )
            staged_executable.chmod(0o755)
            staged_executable.replace(EXECUTABLE)
            (staging_path / "Copying.txt").replace(INSTALL_DIR / "Copying.txt")

    EXECUTABLE_LINK.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".stockfish-link-", dir=EXECUTABLE_LINK.parent
    ) as temp_dir:
        staged_link = Path(temp_dir) / EXECUTABLE_LINK.name
        staged_link.symlink_to(EXECUTABLE)
        staged_link.replace(EXECUTABLE_LINK)


if __name__ == "__main__":
    main()
