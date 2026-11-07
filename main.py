"""ChessHelper - real-time chess.com move assistant (arrow overlay).

Press F10 to calibrate, then the overlay shows Stockfish's best move
as an arrow on top of the board whenever it's your turn.
"""
import contextlib
import ctypes
import json
import os
import queue
import shutil
import sys
import threading
import time

import chess
from pynput import keyboard

import board as bm
import calibrate
import capture
import engine as eng
import overlay as ovl

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
LOCAL_STOCKFISH = os.path.join(
    BASE, "stockfish", "stockfish.exe" if sys.platform == "win32" else "stockfish")

if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


def stockfish_path():
    """Find Stockfish from an override, local setup, or the system PATH."""
    candidates = [
        os.environ.get("CHESSHELPER_STOCKFISH"),
        LOCAL_STOCKFISH,
        shutil.which("stockfish"),
        "/opt/homebrew/bin/stockfish",
        "/usr/local/bin/stockfish",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return LOCAL_STOCKFISH


def macos_permissions_ready():
    """Request the two macOS permissions needed by capture and hotkeys."""
    if sys.platform != "darwin":
        return True
    try:
        import Quartz
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        screen = bool(Quartz.CGPreflightScreenCaptureAccess())
        if not screen:
            Quartz.CGRequestScreenCaptureAccess()
        accessibility = bool(AXIsProcessTrustedWithOptions({
            kAXTrustedCheckOptionPrompt: True,
        }))
        if screen and accessibility:
            return True
        print()
        print("ChessHelper needs Screen Recording and Accessibility access.")
        print("Enable both in System Settings > Privacy & Security, then")
        print("close this Terminal window and run start.command again.")
        return False
    except Exception as error:
        print("Could not check macOS permissions:", error)
        print("Run start.command again after dependencies finish installing.")
        return False


def load_cfg():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("rect", None)
    cfg.setdefault("color", "white")
    cfg.setdefault("think_ms", 500)
    rect = cfg.get("rect")
    if (not isinstance(rect, list) or len(rect) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       for v in rect)
            or rect[2] <= rect[0] or rect[3] <= rect[1]):
        cfg["rect"] = None
    else:
        # mss expects integral physical-pixel coordinates. JSON permits
        # floating-point values, so normalize valid hand-edited configs here
        # instead of failing repeatedly in the capture loop.
        rounded = [round(v) for v in rect]
        cfg["rect"] = calibrate.normalize_rect(
            [(rounded[0], rounded[1]), (rounded[2], rounded[3])])
    if cfg["color"] not in ("white", "black"):
        cfg["color"] = "white"
    try:
        cfg["think_ms"] = max(100, min(10000, int(cfg["think_ms"])))
    except (TypeError, ValueError):
        cfg["think_ms"] = 500
    return cfg


def save_cfg(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class Assistant:
    def __init__(self):
        self.cfg = load_cfg()
        self.running = True
        self.paused = False
        self.ui = queue.Queue()
        self.overlay = None
        self.engine = None
        self.worker = None
        self.calibration_pending = False
        self.templates = bm.load_template_sets()
        self.prev_fen = None
        self.position = None
        self.pending_move = None
        self.waiting_for = None
        self.stable_fen = None
        self.stable_count = 0
        self.last_status = None

    # ---------- overlay plumbing (main thread) ----------
    def post(self, fn):
        self.ui.put(fn)

    def status(self, text):
        if text != self.last_status:
            self.last_status = text
            self.post(lambda text=text: self.overlay.show_status(text))

    def poll(self):
        while True:
            try:
                fn = self.ui.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                print("UI callback error:", e)
        if self.running:
            with contextlib.suppress(Exception):
                self.overlay.root.after(50, self.poll)

    # ---------- capture / analyze loop (worker thread) ----------
    def tick(self):
        if self.paused:
            self.status("Paused — press F9 to resume")
            return
        if (sys.platform in ("win32", "darwin")
                and not self.overlay.capture_excluded):
            self.status(
                "Overlay capture protection unavailable — restart ChessHelper")
            return
        rect = self.cfg.get("rect")
        if not rect:
            self.status("Not calibrated — press F10, set grid, click SAVE GRID")
            return
        if not self.templates:
            self.status("Piece images missing — close app and run setup.py")
            return
        # Keep the current suggestion visible between position changes instead
        # of replacing it with this transient message every scan.
        if self.prev_fen is None and self.stable_fen is None:
            self.status("Reading board… wait for squares to settle")
        img = capture.grab(rect)
        cells = bm.grid_cells(img)
        grid = bm.classify_grid(cells, self.templates)
        fen_part = bm.cells_to_fen(grid, self.cfg["color"])
        if not bm.valid_placement(fen_part):
            self.stable_fen = None
            self.stable_count = 0
            self.status("Board not recognized — recalibrate to the outer square edges")
            return
        if fen_part == self.stable_fen:
            self.stable_count += 1
        else:
            self.stable_fen = fen_part
            self.stable_count = 1
            self.status("Board detected — stabilizing position…")
            return
        # Reject brief drag/animation frames without adding much latency.
        if self.stable_count < 3:
            return
        if fen_part == self.prev_fen:
            if self.waiting_for == "analysis" and self.position is not None:
                self._suggest_move(self.cfg["color"][0])
            return
        self.on_new_position(fen_part)

    def on_new_position(self, fen_part):
        """Accept only legal turn-by-turn changes before replacing an arrow."""
        me = self.cfg["color"][0]
        start = chess.STARTING_FEN.split(" ")[0]

        if self.position is None:
            self.prev_fen = fen_part
            if fen_part == start and me == "b":
                self.position = bm.make_board(fen_part, "w")
                self.waiting_for = "opponent"
                self.status("Position read — waiting for opponent")
                return
            self.position = bm.make_board(fen_part, me)
            if fen_part != start:
                # Screenshot-only attachment cannot know whether an unmoved
                # king/rook moved earlier. Never suggest an illegal castle.
                self.position.castling_rights = 0
            self._suggest_move(me)
            return

        target = bm.make_board(fen_part, me).piece_map()
        if self.waiting_for == "our_move":
            move = bm.find_move(self.position, target)
            if move is not None:
                self.position.push(move)
                self.prev_fen = fen_part
                self.waiting_for = "opponent"
                self.pending_move = None
                self.post(self.overlay.clear)
                self.status("Move confirmed — waiting for opponent")
                return
            moves = bm.find_two_moves(self.position, target)
            if moves is not None:
                # A fast opponent or bot replied between screen reads.
                self.position.push(moves[0])
                self.position.push(moves[1])
                self.prev_fen = fen_part
                self._suggest_move(me)
                return

        if self.waiting_for == "opponent":
            move = bm.find_move(self.position, target)
            if move is not None:
                self.position.push(move)
                self.prev_fen = fen_part
                self._suggest_move(me)
                return

        # A completed game followed by a fresh board is not a legal one-move
        # transition. Resynchronize instead of ignoring the new game forever.
        if fen_part == start and self.prev_fen != start:
            self._reset_position_tracking()
            self.on_new_position(fen_part)
        # Otherwise this is animation or recognition noise; keep the arrow.

    def _suggest_move(self, me):
        self.waiting_for = "analysis"
        self.status("Your turn — Stockfish is calculating…")
        best, score, mate = self.engine.analyze(
            self.position.fen(), self.cfg["think_ms"])
        if not best:
            self.post(self.overlay.clear)
            self.pending_move = None
            self.waiting_for = None
            self.status("No legal move found — check whether the game ended")
            return

        mv = chess.Move.from_uci(best)
        try:
            san = self.position.san(mv)
        except Exception:
            san = best
        if mate is not None:
            label = f"{san}  M{mate}"
        elif score is not None:
            label = f"{san}  {score:+.1f}"
        else:
            label = san
        x1, y1 = bm.square_center(
            mv.from_square, self.cfg["rect"], self.cfg["color"])
        x2, y2 = bm.square_center(
            mv.to_square, self.cfg["rect"], self.cfg["color"])
        self.post(lambda x1=x1, y1=y1, x2=x2, y2=y2, label=label:
                  self.overlay.show_arrow(x1, y1, x2, y2, label))
        self.pending_move = mv
        self.waiting_for = "our_move"
        self.last_status = "move:" + best
        self.status(f"Suggested move: {san} — follow the arrow")

    def _reset_position_tracking(self):
        self.prev_fen = None
        self.position = None
        self.pending_move = None
        self.waiting_for = None

    def loop(self):
        while self.running:
            try:
                self.tick()
            except Exception as e:
                print("tick error:", e)
                self.status(f"Read error: {str(e)[:80]}")
            time.sleep(0.15)

    # ---------- calibration ----------
    def _do_calibrate(self):
        was_paused = self.paused
        self.paused = True
        error = None
        rect = None
        try:
            # Keep the overlay mapped while calibration is open. This ensures
            # a cancellation or Tk error cannot strand the app invisibly.
            rect = calibrate.run(parent=self.overlay.root)
        except Exception as e:
            print("calibration failed:", e)
            error = e
        finally:
            self.paused = was_paused
            self.calibration_pending = False
            try:
                self.overlay.root.deiconify()
                self.overlay.root.lift()
            except Exception:
                pass
        if error is not None:
            self.last_status = None
            self.status(f"Calibration failed: {str(error)[:70]} — press F10 to retry")
            return
        if rect:
            self.cfg["rect"] = rect
            save_cfg(self.cfg)
            self.overlay.set_board_rect(rect)
            self.prev_fen = None
            self.position = None
            self.stable_fen = None
            self.stable_count = 0
            self.pending_move = None
            self.waiting_for = None
            self.post(self.overlay.clear)
            print("calibration saved:", rect)
            self.last_status = None
            self.status("Calibration saved — reading board…")
        else:
            self.last_status = None
            self.status("Calibration cancelled — press F10 to retry")

    # ---------- hotkeys ----------
    def on_hotkey(self, key_name):
        if key_name == "f9":
            self.paused = not self.paused
            print("assistant:", "paused" if self.paused else "running")
            self.last_status = None
        elif key_name == "f10":
            if self.calibration_pending:
                return
            self.calibration_pending = True
            print(
                "calibrating: click outer top-left and bottom-right "
                "edges of the board;")
            print("check the green grid, then press Enter to accept")
            self.post(self._do_calibrate)
        elif key_name == "f11":
            self.cfg["color"] = "black" if self.cfg["color"] == "white" else "white"
            save_cfg(self.cfg)
            self.prev_fen = None
            self.position = None
            self.stable_fen = None
            self.stable_count = 0
            self.pending_move = None
            self.waiting_for = None
            self.post(self.overlay.clear)
            print("playing as:", self.cfg["color"])
        elif key_name == "f8":
            self.shutdown()

    def start_hotkeys(self):
        def handler(name):
            return lambda: self.on_hotkey(name)

        with keyboard.GlobalHotKeys({
            "<f8>": handler("f8"),
            "<f9>": handler("f9"),
            "<f10>": handler("f10"),
            "<f11>": handler("f11"),
        }) as h:
            h.join()

    # ---------- lifecycle ----------
    def shutdown(self):
        self.running = False
        if self.overlay:
            self.post(self.overlay.root.quit)

    def start(self):
        self.overlay = ovl.Overlay()
        capture.set_excluded_window(self.overlay.capture_window_id)
        self.overlay.set_board_rect(self.cfg.get("rect"))
        if not self.templates:
            print("Piece templates missing or incomplete.")
            print("Run: .venv\\Scripts\\python setup.py")
            self.running = False
            self.overlay.destroy()
            return
        try:
            self.engine = eng.Engine(stockfish_path())
        except FileNotFoundError as e:
            print(e)
            print("Run: .venv\\Scripts\\python setup.py")
            self.running = False
            self.overlay.destroy()
            return
        self.worker = threading.Thread(target=self.loop, daemon=True)
        self.worker.start()
        threading.Thread(target=self.start_hotkeys, daemon=True).start()
        self.overlay.root.after(50, self.poll)
        if not self.cfg.get("rect"):
            print("Board not calibrated. Opening calibration now.")
            print(
                "Click the two outer board corners, check the green grid, "
                "then save it.")
            # A new user cannot receive an arrow until the app knows the exact
            # board bounds. Open calibration automatically instead of leaving
            # them at a status message that requires discovering F10 first.
            self.calibration_pending = True
            self.overlay.root.after(400, self._do_calibrate)
        print("hotkeys: F9 pause | F10 calibrate | F11 switch color | F8 quit")
        try:
            self.overlay.root.mainloop()
        finally:
            self.running = False
            if self.worker and self.worker.is_alive():
                self.worker.join(timeout=3)
            if self.engine:
                self.engine.quit()
            self.overlay.destroy()


def main():
    if not macos_permissions_ready():
        return
    Assistant().start()


if __name__ == "__main__":
    main()

