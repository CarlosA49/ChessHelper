"""One-time setup: download Stockfish and chess.com piece templates."""
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
SF_DIR = os.path.join(BASE, "stockfish")
TEMPLATES_DIR = os.path.join(BASE, "templates")

STOCKFISH_URLS = [
    "https://github.com/official-stockfish/Stockfish/releases/download/sf_17.1/stockfish-windows-x86-64-avx2.zip",
    "https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-windows-x86-64-avx2.zip",
    "https://stockfishchess.org/files/stockfish_17.1_win_x64_avx2.zip",
]
# Keep the recognizer aligned with the themes users can select on chess.com.
# The neon set is visually unrelated to the default 2D sets, so it needs its
# own templates rather than relying on shape matching against neo/classic.
TEMPLATE_SETS = ["neo", "classic", "neon"]
# Event/bot piece sets use opaque asset IDs instead of public theme names.
ASSET_TEMPLATE_SETS = {"cyber_event": "nw662"}
UA = {"User-Agent": "Mozilla/5.0 (ChessHelper setup)"}


def valid_template(path):
    """Return whether a downloaded image has the alpha channel we require."""
    try:
        import cv2
    except ImportError:
        return False
    try:
        image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        return image is not None and image.ndim == 3 and image.shape[2] == 4
    except (OSError, cv2.error):
        return False


def fetch(url, dest):
    req = urllib.request.Request(url, headers=UA)
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=90) as resp, open(tmp, "wb") as f:
            f.write(resp.read())
        if os.path.getsize(tmp) == 0:
            raise RuntimeError("download returned an empty file")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def setup_stockfish():
    if sys.platform == "darwin":
        existing = (shutil.which("stockfish")
                    or next((path for path in (
                        "/opt/homebrew/bin/stockfish",
                        "/usr/local/bin/stockfish",
                    ) if os.path.isfile(path)), None))
        if existing:
            print("[stockfish] system installation found:", existing)
            return True
        brew = shutil.which("brew")
        if not brew:
            print("[stockfish] Homebrew is required on macOS.")
            print("Install it from https://brew.sh and run start.command again.")
            return False
        print("[stockfish] installing with Homebrew...")
        result = subprocess.run([brew, "install", "stockfish"], check=False)
        installed = (shutil.which("stockfish")
                     or next((path for path in (
                         "/opt/homebrew/bin/stockfish",
                         "/usr/local/bin/stockfish",
                     ) if os.path.isfile(path)), None))
        if result.returncode == 0 and installed:
            print("[stockfish] Homebrew installation ready")
            return True
        print("[stockfish] Homebrew could not install Stockfish.")
        return False

    exe = os.path.join(SF_DIR, "stockfish.exe")
    if os.path.isfile(exe):
        print("[stockfish] already present:", exe)
        return True
    os.makedirs(SF_DIR, exist_ok=True)
    for url in STOCKFISH_URLS:
        try:
            print("[stockfish] downloading:", url)
            zpath = os.path.join(SF_DIR, "sf.zip")
            fetch(url, zpath)
            with zipfile.ZipFile(zpath) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".exe")]
                target = None
                for n in names:
                    if "stockfish" in os.path.basename(n).lower():
                        target = n
                        break
                if target is None and names:
                    target = names[0]
                if target is None:
                    raise RuntimeError("no .exe inside the archive")
                with z.open(target) as src, open(exe, "wb") as out:
                    out.write(src.read())
            os.remove(zpath)
            print("[stockfish] ready:", exe)
            return True
        except (OSError, RuntimeError, urllib.error.URLError,
                zipfile.BadZipFile) as e:
            print("[stockfish] failed:", e)
    print("[stockfish] automatic download failed. Download it manually from")
    print("  https://stockfishchess.org/download/")
    print(f"and put stockfish.exe into: {SF_DIR}")
    return False


def setup_templates():
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    complete_sets = 0
    for set_name in TEMPLATE_SETS:
        sdir = os.path.join(TEMPLATES_DIR, set_name)
        os.makedirs(sdir, exist_ok=True)
        ok = True
        for color in "wb":
            for p in "pnbrqk":
                fp = os.path.join(sdir, f"{color}{p}.png")
                if os.path.isfile(fp) and valid_template(fp):
                    continue
                if os.path.isfile(fp):
                    os.remove(fp)
                url = (f"https://www.chess.com/chess-themes/pieces/"
                       f"{set_name}/150/{color}{p}.png")
                try:
                    fetch(url, fp)
                    if not valid_template(fp):
                        os.remove(fp)
                        raise RuntimeError("downloaded image is not a valid RGBA PNG")
                except (OSError, RuntimeError, urllib.error.URLError) as e:
                    print(f"[templates] {set_name}/{color}{p} failed: {e}")
                    ok = False
        print(f"[templates] set '{set_name}': {'ok' if ok else 'incomplete'}")
        complete_sets += int(ok)
    for set_name, asset_id in ASSET_TEMPLATE_SETS.items():
        sdir = os.path.join(TEMPLATES_DIR, set_name)
        os.makedirs(sdir, exist_ok=True)
        ok = True
        for color in "wb":
            for p in "pnbrqk":
                fp = os.path.join(sdir, f"{color}{p}.png")
                if os.path.isfile(fp) and valid_template(fp):
                    continue
                if os.path.isfile(fp):
                    os.remove(fp)
                url = (f"https://assets-themes.chess.com/image/"
                       f"{asset_id}/150/{color}{p}.png")
                try:
                    fetch(url, fp)
                    if not valid_template(fp):
                        os.remove(fp)
                        raise RuntimeError("downloaded image is not a valid RGBA PNG")
                except (OSError, RuntimeError, urllib.error.URLError) as e:
                    print(f"[templates] {set_name}/{color}{p} failed: {e}")
                    ok = False
        print(f"[templates] set '{set_name}': {'ok' if ok else 'incomplete'}")
        complete_sets += int(ok)
    return complete_sets > 0


def main():
    ok_sf = setup_stockfish()
    ok_templates = setup_templates()
    if not ok_templates:
        print("[templates] no complete piece set is available")
    if not ok_sf or not ok_templates:
        sys.exit(1)
    print("setup complete.")


if __name__ == "__main__":
    main()
