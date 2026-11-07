#!/bin/bash
set -u

cd "$(dirname "$0")" || exit 1

fail() {
  echo
  echo "Setup failed. Review the message above, then press Return to close."
  read -r
  exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/macos/"
  fail
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating Python environment..."
  python3 -m venv .venv || fail
fi

source .venv/bin/activate || fail

if ! python -c "import tkinter" >/dev/null 2>&1; then
  echo "This Python installation does not include Tkinter."
  echo "Install Python from python.org, or install the matching python-tk formula."
  fail
fi

echo "Installing/updating Python dependencies..."
python -m pip install --disable-pip-version-check -q -r requirements.txt || fail

python setup.py || fail

echo
echo "Starting ChessHelper. On first launch, macOS will request Screen Recording"
echo "and Accessibility access. Grant both, then run this file once more."
python main.py
status=$?

if [ "$status" -ne 0 ]; then
  fail
fi
