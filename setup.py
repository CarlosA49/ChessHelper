"""One-time setup: download Stockfish and chess.com piece templates."""
import os
import sys
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
        except Exception as e:
            print("[stockfish] failed:", e)
    print("[stockfish] automatic download failed. Download it manually from")
    print("  https://stockfishchess.org/download/")
    print(f"and put stockfish.exe into: {SF_DIR}")
    return False


def setup_templates():
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    for set_name in TEMPLATE_SETS:
        sdir = os.path.join(TEMPLATES_DIR, set_name)
        os.makedirs(sdir, exist_ok=True)
        ok = True
        for color in "wb":
            for p in "pnbrqk":
                fp = os.path.join(sdir, f"{color}{p}.png")
                if os.path.isfile(fp):
                    try:
                        import cv2
                        if cv2.imread(fp, cv2.IMREAD_UNCHANGED) is not None:
                            continue
                    except Exception:
                        pass
                    os.remove(fp)
                url = (f"https://www.chess.com/chess-themes/pieces/"
                       f"{set_name}/150/{color}{p}.png")
                try:
                    fetch(url, fp)
                except Exception as e:
                    print(f"[templates] {set_name}/{color}{p} failed: {e}")
                    ok = False
        print(f"[templates] set '{set_name}': {'ok' if ok else 'incomplete'}")
    for set_name, asset_id in ASSET_TEMPLATE_SETS.items():
        sdir = os.path.join(TEMPLATES_DIR, set_name)
        os.makedirs(sdir, exist_ok=True)
        ok = True
        for color in "wb":
            for p in "pnbrqk":
                fp = os.path.join(sdir, f"{color}{p}.png")
                if os.path.isfile(fp):
                    try:
                        import cv2
                        if cv2.imread(fp, cv2.IMREAD_UNCHANGED) is not None:
                            continue
                    except Exception:
                        pass
                    os.remove(fp)
                url = (f"https://assets-themes.chess.com/image/"
                       f"{asset_id}/150/{color}{p}.png")
                try:
                    fetch(url, fp)
                except Exception as e:
                    print(f"[templates] {set_name}/{color}{p} failed: {e}")
                    ok = False
        print(f"[templates] set '{set_name}': {'ok' if ok else 'incomplete'}")


def main():
    ok_sf = setup_stockfish()
    setup_templates()
    if not ok_sf:
        sys.exit(1)
    print("setup complete.")


if __name__ == "__main__":
    main()
