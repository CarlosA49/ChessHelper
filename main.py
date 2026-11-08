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
from pynput import keyboard, mouse

import board as bm
import calibrate
import capture
import engine as eng
import overlay as ovl

SOURCE_BASE = os.path.dirname(os.path.abspath(__file__))
BASE = getattr(sys, "_MEIPASS", SOURCE_BASE)


def default_config_path(platform=None, frozen=None, environ=None, home=None):
    """Return a writable config path for source and packaged applications."""
    platform = platform or sys.platform
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return os.path.join(SOURCE_BASE, "config.json")
    environ = os.environ if environ is None else environ
    home = os.path.expanduser("~") if home is None else home
    if platform == "darwin":
        root = os.path.join(home, "Library", "Application Support")
    elif platform == "win32":
        root = environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
    else:
        root = environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(root, "ChessHelper", "config.json")


CONFIG = os.environ.get("CHESSHELPER_CONFIG", default_config_path())
LOCAL_STOCKFISH = os.path.join(
    BASE, "stockfish", "stockfish.exe" if sys.platform == "win32" else "stockfish")
CONFIG_LOCK = threading.Lock()


def side_shortcuts(platform=None):
    """Return native side-control hotkeys and their display labels."""
    platform = platform or sys.platform
    if platform == "darwin":
        return (
            "<cmd>+<alt>+b", "<cmd>+<alt>+a",
            "Cmd+Option+B", "Cmd+Option+A",
        )
    return (
        "<ctrl>+<alt>+b", "<ctrl>+<alt>+a",
        "Ctrl+Alt+B", "Ctrl+Alt+A",
    )


def auto_move_shortcut(platform=None):
    """Return the native auto-move hotkey and display label."""
    platform = platform or sys.platform
    if platform == "darwin":
        return "<cmd>+<alt>+m", "Cmd+Option+M"
    return "<ctrl>+<alt>+m", "Ctrl+Alt+M"

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
    cfg.setdefault("auto_color", True)
    cfg.setdefault("auto_move", False)
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
    if not isinstance(cfg["auto_color"], bool):
        cfg["auto_color"] = True
    if not isinstance(cfg["auto_move"], bool):
        cfg["auto_move"] = False
    # Automatic clicking always requires an explicit opt-in for this session.
    cfg["auto_move"] = False
    try:
        cfg["think_ms"] = max(100, min(10000, int(cfg["think_ms"])))
    except (TypeError, ValueError):
        cfg["think_ms"] = 500
    return cfg


def save_cfg(cfg):
    """Atomically persist configuration without exposing partial JSON."""
    temporary = CONFIG + ".tmp"
    with CONFIG_LOCK:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, CONFIG)


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
        self.template_index = None
        self.prev_fen = None
        self.position = None
        self.pending_move = None
        self.waiting_for = None
        self.stable_fen = None
        self.stable_count = 0
        self.invalid_count = 0
        self.rejected_fen = None
        self.rejected_count = 0
        self.last_move_status = None
        self.last_board_image = None
        self.last_relocation_attempt = 0.0
        self.relocation_failures = 0
        self.auto_move_in_progress = False
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
    def _placement_priority(self, fen_part):
        """Prefer observations consistent with the tracked legal position."""
        if not bm.valid_placement(fen_part):
            return 100
        if self.position is None or fen_part == self.prev_fen:
            return 0
        target = bm.make_board(fen_part, self.cfg["color"][0]).piece_map()
        return 1 if bm.find_move(self.position, target) is not None else 10

    def _classify_observation(self, cells):
        """Classify with the active theme, falling back to legal candidates."""
        indices = None
        if self.template_index is not None:
            indices = [self.template_index]
        candidates = bm.classify_grid_candidates(
            cells, self.templates, indices=indices)

        observations = []
        for _invalid, error, index, grid in candidates:
            orientation = self.cfg["color"]
            if self.cfg["auto_color"] and self.position is None:
                orientation, fen_part = bm.detect_orientation(
                    grid, self.cfg["color"])
            else:
                fen_part = bm.cells_to_fen(grid, self.cfg["color"])
            observations.append((
                self._placement_priority(fen_part), error, index,
                orientation, fen_part, grid,
            ))

        # If the locked theme cannot form a plausible placement, compare all
        # themes immediately rather than waiting through invalid-frame retries.
        if (self.template_index is not None
                and (not observations or observations[0][0] >= 100)):
            candidates = bm.classify_grid_candidates(cells, self.templates)
            observations = []
            for _invalid, error, index, grid in candidates:
                orientation = self.cfg["color"]
                if self.cfg["auto_color"] and self.position is None:
                    orientation, fen_part = bm.detect_orientation(
                        grid, self.cfg["color"])
                else:
                    fen_part = bm.cells_to_fen(grid, self.cfg["color"])
                observations.append((
                    self._placement_priority(fen_part), error, index,
                    orientation, fen_part, grid,
                ))

        if not observations:
            empty = [[None for _ in range(8)] for _ in range(8)]
            return self.cfg["color"], "8/8/8/8/8/8/8/8", empty
        _priority, _error, index, orientation, fen_part, grid = min(
            observations, key=lambda item: (item[0], item[1]))
        self.template_index = index
        return orientation, fen_part, grid

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
        detected_orientation, fen_part, _grid = self._classify_observation(cells)
        if not bm.valid_placement(fen_part):
            self.invalid_count = min(self.invalid_count + 1, 1000)
            self.stable_count = 0
            if self.invalid_count == 4:
                self.status("Checking board — retrying automatically…")
            elif self.invalid_count == 40:
                self.status(
                    "Board still unreadable — keep it visible; "
                    "press F10 only if it moved")
            relocation_delay = min(
                120, 15 * (2 ** min(self.relocation_failures, 3)))
            if (self.invalid_count >= 20
                    and time.monotonic() - self.last_relocation_attempt
                    >= relocation_delay):
                if self.last_board_image is None:
                    self.status(
                        "Saved board area is stale — scanning the screen…")
                self._try_relocate_board()
            return
        self.invalid_count = 0
        self.relocation_failures = 0
        if fen_part == self.stable_fen:
            self.stable_count = min(self.stable_count + 1, 3)
        else:
            self.stable_fen = fen_part
            self.stable_count = 1
            self.status("Board detected — stabilizing position…")
            return
        # Reject brief drag/animation frames without adding much latency.
        if self.stable_count < 2:
            return
        # Relocation must use a position that survived stabilization, not a
        # single legal-looking animation frame.
        self.last_board_image = img
        if (self.cfg["auto_color"] and self.position is None
                and detected_orientation != self.cfg["color"]):
            self.cfg["color"] = detected_orientation
            save_cfg(self.cfg)
            self._refresh_controls()
            print("board orientation detected:", detected_orientation)
        if fen_part == self.prev_fen:
            if self.waiting_for == "analysis" and self.position is not None:
                self._suggest_move(self.cfg["color"][0])
            else:
                self._restore_status()
            return
        self.on_new_position(fen_part)

    def _try_relocate_board(self):
        """Silently search, then adopt only a twice-verified moved board."""
        self.last_relocation_attempt = time.monotonic()
        original_template = self.template_index
        adopted = False
        try:
            desktop, desktop_rect = capture.grab_virtual_screen()
            if self.last_board_image is None:
                candidates = capture.find_chessboard_rects(
                    desktop, desktop_rect)
            else:
                candidate = capture.find_relocated_rect(
                    self.last_board_image, desktop, desktop_rect)
                candidates = [candidate] if candidate else []
            if not candidates:
                return False

            for candidate in candidates:
                if (self.last_board_image is not None
                        and capture.rects_are_near(
                            candidate, self.cfg.get("rect"))):
                    continue
                self.template_index = original_template
                verified = self._verify_board_candidate(candidate)
                if verified is None:
                    continue
                orientation, confirmed_img = verified
                self.cfg["rect"] = candidate
                color_changed = False
                if self.cfg["auto_color"]:
                    color_changed = self.cfg["color"] != orientation
                    self.cfg["color"] = orientation
                save_cfg(self.cfg)
                if color_changed:
                    self._refresh_controls()
                self.last_board_image = confirmed_img
                self.invalid_count = 0
                self.relocation_failures = 0
                self.stable_fen = None
                self.stable_count = 0
                if self.waiting_for == "our_move":
                    # The existing arrow uses the old pixel rectangle. Request
                    # fresh analysis so it is redrawn against the new board.
                    self.waiting_for = "analysis"
                    self.pending_move = None
                    self.last_move_status = None
                    self.post(self.overlay.clear)
                self.post(
                    lambda rect=candidate: self.overlay.set_board_rect(rect))
                self.status("Board location verified — reading position…")
                print("board located automatically:", candidate)
                adopted = True
                return True
            return False
        except Exception as error:
            print("automatic board relocation failed:", error)
            return False
        finally:
            if not adopted:
                self.template_index = original_template
                self.relocation_failures = min(
                    self.relocation_failures + 1, 4)

    def _verify_board_candidate(self, candidate):
        """Return orientation and image after two stable, legal reads."""
        candidate_img = capture.grab(candidate)
        orientation, first_fen, _grid = self._classify_observation(
            bm.grid_cells(candidate_img))
        if (not bm.valid_placement(first_fen)
                or not self._relocation_position_plausible(first_fen)):
            return None

        # A dialog, animation, or unrelated board can produce one convincing
        # match. Require a second independent read before saving coordinates.
        time.sleep(0.06)
        confirmed_img = capture.grab(candidate)
        confirmed_orientation, second_fen, _grid = (
            self._classify_observation(bm.grid_cells(confirmed_img)))
        if (second_fen != first_fen
                or confirmed_orientation != orientation
                or not bm.valid_placement(second_fen)
                or not self._relocation_position_plausible(second_fen)):
            return None
        return orientation, confirmed_img

    def _relocation_position_plausible(self, fen_part):
        """Reject unrelated valid chessboards found elsewhere on screen."""
        if self.position is None or fen_part == self.prev_fen:
            return True
        target = bm.make_board(fen_part, self.cfg["color"][0]).piece_map()
        if bm.find_move_sequence(self.position, target, max_plies=3) is not None:
            return True
        start = chess.STARTING_FEN.split(" ")[0]
        if fen_part == start:
            return True
        old_count = len(self.position.piece_map())
        new_count = len(target)
        return new_count >= 20 and new_count >= old_count + 4

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
                self._clear_rejected_position()
                return
            moves = bm.find_two_moves(self.position, target)
            if moves is not None:
                # A fast opponent or bot replied between screen reads.
                self.position.push(moves[0])
                self.position.push(moves[1])
                self.prev_fen = fen_part
                self._clear_rejected_position()
                self._suggest_move(me)
                return

        if self.waiting_for == "opponent":
            move = bm.find_move(self.position, target)
            if move is not None:
                self.position.push(move)
                self.prev_fen = fen_part
                self._clear_rejected_position()
                self._suggest_move(me)
                return

        if fen_part == self.rejected_fen:
            self.rejected_count = min(self.rejected_count + 1, 1000)
        else:
            self.rejected_fen = fen_part
            self.rejected_count = 1

        # A new game can already have its first move on screen, so exact start
        # position matching alone is insufficient after fast rematches.
        if fen_part == start and self.prev_fen != start:
            self._reset_position_tracking()
            self.post(self.overlay.clear)
            self.on_new_position(fen_part)
            return

        # If capture or analysis briefly stalled, several half-moves may have
        # passed between accepted frames. Recover the legal history instead of
        # remaining permanently stuck on a stable, newer position.
        if self.rejected_count == 3:
            sequence = bm.find_move_sequence(
                self.position, target, max_plies=3)
            if sequence:
                for move in sequence:
                    self.position.push(move)
                self.prev_fen = fen_part
                self.pending_move = None
                self._clear_rejected_position()
                if self.position.turn == (me == "w"):
                    self._suggest_move(me)
                else:
                    self.waiting_for = "opponent"
                    self.post(self.overlay.clear)
                    self.status(
                        "Position resynchronized — waiting for opponent")
                return
        old_count = len(self.position.piece_map())
        new_count = len(target)
        definitely_new = (
            self.position.is_game_over(claim_draw=False)
            or (new_count >= 20 and new_count >= old_count + 4)
        )
        if definitely_new and self.rejected_count >= 3:
            self._reset_position_tracking()
            self.post(self.overlay.clear)
            self.stable_fen = None
            self.stable_count = 0
            self.status("New game detected — reading board and side automatically…")
            return
        # Keep the existing arrow during recognition noise, but do not leave
        # the status stuck on "stabilizing" indefinitely.
        if self.rejected_count >= 3:
            # The visually closest theme produced no legal continuation. Force
            # a full-theme comparison on the next capture.
            self.template_index = None
            self.status("Checking board — retrying automatically…")

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
        self.last_move_status = f"Suggested move: {san} — follow the arrow"
        self.last_status = "move:" + best
        self.status(self.last_move_status)
        self._start_auto_move(mv)

    def _start_auto_move(self, move):
        """Click a suggested move when the explicit auto-move mode is on."""
        if (not self.cfg.get("auto_move") or self.auto_move_in_progress
                or self.paused or not self.running):
            return None
        if move.promotion is not None:
            self.status(
                "Promotion ready — move manually to choose the promoted piece")
            return None

        start = bm.square_center(
            move.from_square, self.cfg["rect"], self.cfg["color"])
        end = bm.square_center(
            move.to_square, self.cfg["rect"], self.cfg["color"])
        self.auto_move_in_progress = True

        def perform():
            try:
                time.sleep(0.04)
                if (not self.cfg.get("auto_move") or self.paused
                        or self.pending_move != move
                        or self.waiting_for != "our_move"):
                    return
                controller = mouse.Controller()
                controller.position = start
                controller.click(mouse.Button.left)
                time.sleep(0.04)
                if (not self.cfg.get("auto_move") or self.paused
                        or self.pending_move != move
                        or self.waiting_for != "our_move"):
                    return
                controller.position = end
                controller.click(mouse.Button.left)
                self.status("Move played automatically — confirming board…")
            except Exception as error:
                print("automatic move failed:", error)
                self.status(
                    "Automatic move failed — play manually or disable auto-move")
            finally:
                self.auto_move_in_progress = False

        worker = threading.Thread(target=perform, daemon=True)
        worker.start()
        return worker

    def _restore_status(self):
        """Restore useful state text after temporary capture noise."""
        if self.waiting_for == "our_move":
            self.status(self.last_move_status or "Move ready — follow the arrow")
        elif self.waiting_for == "opponent":
            self.status("Position read — waiting for opponent")
        elif self.waiting_for == "analysis":
            self.status("Your turn — Stockfish is calculating…")

    def _clear_rejected_position(self):
        self.rejected_fen = None
        self.rejected_count = 0

    def _reset_position_tracking(self):
        self.prev_fen = None
        self.position = None
        self.pending_move = None
        self.waiting_for = None
        self.last_move_status = None
        self._clear_rejected_position()

    def loop(self):
        try:
            while self.running:
                try:
                    self.tick()
                except Exception as e:
                    print("tick error:", e)
                    self.status(f"Read error: {str(e)[:80]}")
                time.sleep(0.15)
        finally:
            with contextlib.suppress(Exception):
                capture.close_thread_capture()

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
            # A new board size/theme needs a fresh best-template decision.
            self.template_index = None
            save_cfg(self.cfg)
            self.overlay.set_board_rect(rect)
            self._reset_position_tracking()
            self.stable_fen = None
            self.stable_count = 0
            self.invalid_count = 0
            self.last_board_image = None
            self.last_relocation_attempt = 0.0
            self.relocation_failures = 0
            self.post(self.overlay.clear)
            print("calibration saved:", rect)
            self.last_status = None
            self.status("Calibration saved — reading board…")
        else:
            self.last_status = None
            self.status("Calibration cancelled — press F10 to retry")

    # ---------- hotkeys ----------
    def _refresh_controls(self):
        if self.overlay:
            color = self.cfg["color"]
            automatic = self.cfg["auto_color"]
            auto_move = self.cfg["auto_move"]
            self.post(lambda: self.overlay.show_controls(
                color, automatic, auto_move))

    def on_hotkey(self, key_name):
        if key_name == "f7":
            self.post(self.overlay.toggle_controls)
        elif key_name == "f9":
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
        elif key_name == "switch_color":
            self.cfg["auto_color"] = False
            self.cfg["color"] = "black" if self.cfg["color"] == "white" else "white"
            save_cfg(self.cfg)
            self._reset_position_tracking()
            self.stable_fen = None
            self.stable_count = 0
            self.invalid_count = 0
            self.post(self.overlay.clear)
            self._refresh_controls()
            print("playing as:", self.cfg["color"])
            self.last_status = None
            self.status(
                f"Playing as {self.cfg['color']} — manual side override enabled")
        elif key_name == "auto_color":
            self.cfg["auto_color"] = True
            save_cfg(self.cfg)
            self._reset_position_tracking()
            self.stable_fen = None
            self.stable_count = 0
            self.invalid_count = 0
            self.post(self.overlay.clear)
            self._refresh_controls()
            self.last_status = None
            self.status("Automatic side detection enabled — reading board…")
        elif key_name == "auto_move":
            self.cfg["auto_move"] = not self.cfg["auto_move"]
            self._refresh_controls()
            self.last_status = None
            state = "enabled" if self.cfg["auto_move"] else "disabled"
            self.status(f"Automatic move mode {state}")
            if (self.cfg["auto_move"] and self.pending_move is not None
                    and self.waiting_for == "our_move"):
                self._start_auto_move(self.pending_move)
        elif key_name == "f8":
            self.shutdown()

    def start_hotkeys(self):
        def handler(name):
            return lambda: self.on_hotkey(name)

        switch_key, auto_key, _switch_label, _auto_label = side_shortcuts()
        move_key, _move_label = auto_move_shortcut()
        with keyboard.GlobalHotKeys({
            "<f7>": handler("f7"),
            "<f8>": handler("f8"),
            "<f9>": handler("f9"),
            "<f10>": handler("f10"),
            switch_key: handler("switch_color"),
            auto_key: handler("auto_color"),
            move_key: handler("auto_move"),
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
        self.overlay.show_controls(
            self.cfg["color"], self.cfg["auto_color"],
            self.cfg["auto_move"])
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
        _switch_key, _auto_key, switch_label, auto_label = side_shortcuts()
        _move_key, move_label = auto_move_shortcut()
        print("hotkeys: F7 controls | F9 pause | F10 calibrate | "
              f"{switch_label} switch color | {auto_label} auto color | "
              f"{move_label} auto-move | F8 quit")
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

