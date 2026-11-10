#!/bin/bash
set -euo pipefail

bundle_dir="$(cd "$(dirname "$0")" && pwd)"
manifest="$bundle_dir/SHA256SUMS.txt"
install_parent="$HOME/Applications"
install_path="$install_parent/ChessHelper.app"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must be run on macOS."
  exit 1
fi

if [[ "$(sysctl -in hw.optional.arm64 2>/dev/null || echo 0)" == "1" ]]; then
  package_arch="arm64"
else
  package_arch="x64"
fi

archive_name="ChessHelper-macOS-$package_arch.zip"
archive_path="$bundle_dir/$archive_name"
if [[ ! -f "$archive_path" ]]; then
  echo "$archive_name is missing. Extract the complete all-platform ZIP first."
  exit 1
fi
if [[ ! -f "$manifest" ]]; then
  echo "SHA256SUMS.txt is missing from the installer bundle."
  exit 1
fi

expected_hash="$(awk -v name="$archive_name" '$2 == name { print $1; exit }' "$manifest")"
if [[ -z "$expected_hash" ]]; then
  echo "No checksum was provided for $archive_name."
  exit 1
fi
actual_hash="$(shasum -a 256 "$archive_path" | awk '{ print $1 }')"
if [[ "$actual_hash" != "$expected_hash" ]]; then
  echo "The bundled macOS package failed checksum verification."
  exit 1
fi

mkdir -p "$install_parent"
staging_directory="$(mktemp -d "$install_parent/.ChessHelper-install.XXXXXX")"
backup_path=""
cleanup() {
  rm -rf "$staging_directory"
}
trap cleanup EXIT

echo "Installing ChessHelper for macOS $package_arch..."
ditto -x -k "$archive_path" "$staging_directory"
staged_application="$staging_directory/ChessHelper.app"
if [[ ! -x "$staged_application/Contents/MacOS/ChessHelper" ]]; then
  echo "The bundled macOS application is incomplete."
  exit 1
fi
codesign --verify --deep --strict "$staged_application"

pkill -x ChessHelper 2>/dev/null || true
if [[ -d "$install_path" ]]; then
  backup_path="$install_parent/.ChessHelper.backup.$(date +%s).$$"
  mv "$install_path" "$backup_path"
fi

if ! mv "$staged_application" "$install_path"; then
  if [[ -n "$backup_path" && -d "$backup_path" ]]; then
    mv "$backup_path" "$install_path"
  fi
  echo "Installation failed; the previous application was restored."
  exit 1
fi
if [[ -n "$backup_path" && -d "$backup_path" ]]; then
  rm -rf "$backup_path"
fi

xattr -dr com.apple.quarantine "$install_path" 2>/dev/null || true
echo "ChessHelper is installed in $install_path"
echo "Automatic moves can be enabled with Cmd+Option+M."
open "$install_path"
