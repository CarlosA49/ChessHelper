"""Regression tests for the all-platform offline installer bundle."""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.verify_bundle import ARCHIVE_ENTRIES, INSTALLER_FILES, verify_bundle


class DistributionBundleTests(unittest.TestCase):
    def make_bundle(self, root: Path) -> None:
        hashes = []
        for archive_name, entry in ARCHIVE_ENTRIES.items():
            archive = root / archive_name
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr(entry, b"application")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            hashes.append(f"{digest}  {archive_name}")
        for name in INSTALLER_FILES:
            if name != "SHA256SUMS.txt":
                (root / name).write_text("installer", encoding="utf-8")
        (root / "SHA256SUMS.txt").write_text(
            "\n".join(hashes) + "\n", encoding="utf-8")

    def test_complete_bundle_passes_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_bundle(root)
            self.assertEqual(verify_bundle(root), [])

    def test_tampered_package_fails_integrity_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_bundle(root)
            with (root / "ChessHelper-Windows-x64.zip").open("ab") as stream:
                stream.write(b"tampered")
            errors = verify_bundle(root)
            self.assertIn(
                "checksum mismatch: ChessHelper-Windows-x64.zip", errors)

    @unittest.skipUnless(sys.platform == "win32", "Windows installer test")
    def test_windows_installer_replaces_a_user_local_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "install_windows.ps1"
            shutil.copy2(
                Path(__file__).parent / "installer" / "install_windows.ps1",
                script,
            )
            archive = root / "ChessHelper-Windows-x64.zip"
            install_directory = root / "Programs" / "ChessHelper"

            def write_package(payload: bytes) -> None:
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr("ChessHelper/ChessHelper.exe", payload)
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                (root / "SHA256SUMS.txt").write_text(
                    f"{digest}  {archive.name}\n", encoding="utf-8")

            def install() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        "powershell.exe",
                        "-NoLogo",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "-NoLaunch",
                        "-NoShortcuts",
                        "-InstallDirectory",
                        str(install_directory),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            write_package(b"first version")
            first = install()
            self.assertEqual(first.returncode, 0, first.stderr)
            write_package(b"updated version")
            second = install()
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                (install_directory / "ChessHelper.exe").read_bytes(),
                b"updated version",
            )


if __name__ == "__main__":
    unittest.main()
