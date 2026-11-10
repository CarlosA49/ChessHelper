"""Validate the self-contained all-platform release bundle."""
from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

ARCHIVE_ENTRIES = {
    "ChessHelper-Windows-x64.zip": "ChessHelper/ChessHelper.exe",
    "ChessHelper-macOS-arm64.zip": "ChessHelper.app/Contents/MacOS/ChessHelper",
    "ChessHelper-macOS-x64.zip": "ChessHelper.app/Contents/MacOS/ChessHelper",
}
INSTALLER_FILES = (
    "Install ChessHelper on Windows.cmd",
    "install_windows.ps1",
    "Install ChessHelper on macOS.command",
    "INSTALL.txt",
    "SHA256SUMS.txt",
)
HASH_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    hashes = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HASH_LINE.match(line.strip())
        if match:
            hashes[match.group(2)] = match.group(1).lower()
    return hashes


def verify_bundle(bundle: Path) -> list[str]:
    errors = []
    for name in INSTALLER_FILES:
        if not (bundle / name).is_file():
            errors.append(f"missing installer file: {name}")

    manifest_path = bundle / "SHA256SUMS.txt"
    hashes = read_manifest(manifest_path) if manifest_path.is_file() else {}
    for archive_name, required_entry in ARCHIVE_ENTRIES.items():
        archive = bundle / archive_name
        if not archive.is_file():
            errors.append(f"missing application archive: {archive_name}")
            continue
        expected_hash = hashes.get(archive_name)
        if expected_hash is None:
            errors.append(f"missing checksum: {archive_name}")
        elif sha256(archive) != expected_hash:
            errors.append(f"checksum mismatch: {archive_name}")
        try:
            with zipfile.ZipFile(archive) as package:
                entries = {name.replace("\\", "/") for name in package.namelist()}
                if required_entry not in entries:
                    errors.append(
                        f"{archive_name} is missing {required_entry}")
                bad_entry = package.testzip()
                if bad_entry is not None:
                    errors.append(
                        f"{archive_name} has a corrupt entry: {bad_entry}")
        except zipfile.BadZipFile:
            errors.append(f"invalid ZIP archive: {archive_name}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_bundle.py BUNDLE_DIRECTORY", file=sys.stderr)
        return 2
    bundle = Path(sys.argv[1])
    errors = verify_bundle(bundle)
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("bundle verification: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
