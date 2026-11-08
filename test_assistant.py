"""Integration tests for turn-to-arrow behavior without a desktop game."""
import json
import os
import sys
import tempfile
import types
import unittest
import unittest.mock

import chess
import numpy as np

import board as bm
import main
from main import Assistant


class FakeOverlay:
    def __init__(self):
        self.calls = []
        self.root = None
        self.capture_excluded = True

    def clear(self):
        self.calls.append(("clear",))

    def show_status(self, text):
        self.calls.append(("status", text))

    def show_controls(self, color, automatic, auto_move=False):
        self.calls.append(("controls", color, automatic, auto_move))

    def toggle_controls(self):
        self.calls.append(("toggle_controls",))

    def show_arrow(self, x1, y1, x2, y2, label):
        self.calls.append(("arrow", x1, y1, x2, y2, label))

    def set_board_rect(self, rect):
        self.calls.append(("board_rect", rect))


class FakeEngine:
    def __init__(self, moves=None):
        self.moves = list(moves or ["e2e4"])
        self.calls = []

    def analyze(self, fen, movetime_ms):
        self.calls.append((fen, movetime_ms))
        move = self.moves[min(len(self.calls) - 1, len(self.moves) - 1)]
        return move, 0.3, None


class FailOnceEngine(FakeEngine):
    def analyze(self, fen, movetime_ms):
        if not self.calls:
            self.calls.append((fen, movetime_ms))
            raise OSError(22, "temporary engine pipe failure")
        return super().analyze(fen, movetime_ms)


def flush_ui(assistant):
    while not assistant.ui.empty():
        assistant.ui.get_nowait()()


def placement_after(moves):
    board = chess.Board()
    for uci in moves:
        board.push_uci(uci)
    return board.board_fen()


def test_start_position_queues_white_move_arrow():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine()
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"

    assistant.on_new_position(chess.STARTING_FEN.split(" ")[0])

    flush_ui(assistant)
    arrows = [call for call in assistant.overlay.calls if call[0] == "arrow"]
    assert len(arrows) == 1, assistant.overlay.calls
    _, x1, y1, x2, y2, label = arrows[0]
    assert (x1, y1) == bm.square_center(chess.E2, assistant.cfg["rect"], "white")
    assert (x2, y2) == bm.square_center(chess.E4, assistant.cfg["rect"], "white")
    assert "e4" in label


def test_arrow_changes_only_after_our_move_then_opponent_move():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine(["e2e4", "g1f3"])
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"

    start = chess.STARTING_FEN.split(" ")[0]
    after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"
    after_e5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR"
    assistant.on_new_position(start)
    assistant.on_new_position(after_e4)
    assert assistant.waiting_for == "opponent"
    assert len(assistant.engine.calls) == 1
    assistant.on_new_position(after_e5)
    flush_ui(assistant)
    assert assistant.waiting_for == "our_move"
    assert len(assistant.engine.calls) == 2
    arrows = [call for call in assistant.overlay.calls if call[0] == "arrow"]
    assert arrows[-1][1:5] == (
        *bm.square_center(chess.G1, assistant.cfg["rect"], "white"),
        *bm.square_center(chess.F3, assistant.cfg["rect"], "white"))


def test_impossible_position_does_not_replace_current_arrow():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine()
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"
    start = chess.STARTING_FEN.split(" ")[0]
    impossible = "rnbqkbnr/pppppppp/8/8/3PP3/8/PPP2PPP/RNBQKBNR"

    assistant.on_new_position(start)
    assistant.on_new_position(impossible)
    flush_ui(assistant)
    assert assistant.prev_fen == start
    assert assistant.waiting_for == "our_move"
    assert len(assistant.engine.calls) == 1
    assert len([c for c in assistant.overlay.calls if c[0] == "arrow"]) == 1


def test_new_starting_board_resets_late_game_tracking():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine(["e2e4", "d2d4"])
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"
    start = chess.STARTING_FEN.split(" ")[0]

    assistant.on_new_position(start)
    assistant.on_new_position(placement_after(["e2e4"]))
    assistant.on_new_position(start)

    assert assistant.prev_fen == start
    assert assistant.position.board_fen() == start
    assert assistant.waiting_for == "our_move"
    assert len(assistant.engine.calls) == 2


def test_state_preserves_en_passant_history():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine(["e2e4", "e4e5", "e5d6"])
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"

    sequence = []
    assistant.on_new_position(placement_after(sequence))
    for move in ["e2e4", "a7a6", "e4e5", "d7d5"]:
        sequence.append(move)
        assistant.on_new_position(placement_after(sequence))

    assert assistant.position.ep_square == chess.D6
    assert chess.Move.from_uci("e5d6") in assistant.position.legal_moves


def test_midgame_attachment_does_not_invent_castling_rights():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine(["e1g1"])
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"
    placement = "r3k2r/ppp2ppp/2n5/3pp3/3PP3/2N5/PPP2PPP/R3K2R"

    assistant.on_new_position(placement)

    assert assistant.position.castling_rights == 0
    assert chess.Move.from_uci("e1g1") not in assistant.position.legal_moves


def test_failed_analysis_remains_pending_and_can_retry_same_position():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FailOnceEngine(["e2e4"])
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"

    with unittest.TestCase().assertRaises(OSError):
        assistant.on_new_position(chess.STARTING_FEN.split(" ")[0])
    assert assistant.waiting_for == "analysis"
    assistant._suggest_move("w")
    assert assistant.waiting_for == "our_move"
    assert assistant.pending_move == chess.Move.from_uci("e2e4")


def test_float_config_rect_is_normalized_for_screen_capture():
    with tempfile.TemporaryDirectory() as tmp:
        config = os.path.join(tmp, "config.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump({"rect": [100.4, 200.6, 900.2, 1000.8]}, f)
        with unittest.mock.patch.object(main, "CONFIG", config):
            cfg = main.load_cfg()
    assert cfg["rect"] == [100, 201, 900, 1001]


def test_invalid_saved_calibration_is_discarded():
    invalid_rects = (
        [0, 0, 1, 1],
        [0, 0, 800, 200],
        [False, 0, 800, 800],
        [900, 900, 100, 100],
    )
    for rect in invalid_rects:
        with tempfile.TemporaryDirectory() as tmp:
            config = os.path.join(tmp, "config.json")
            with open(config, "w", encoding="utf-8") as f:
                json.dump({"rect": rect}, f)
            with unittest.mock.patch.object(main, "CONFIG", config):
                cfg = main.load_cfg()
        assert cfg["rect"] is None, rect


def test_auto_move_is_always_off_at_application_start():
    with tempfile.TemporaryDirectory() as tmp:
        config = os.path.join(tmp, "config.json")
        with open(config, "w", encoding="utf-8") as f:
            json.dump({"auto_move": True}, f)
        with unittest.mock.patch.object(main, "CONFIG", config):
            cfg = main.load_cfg()
    assert cfg["auto_move"] is False


def test_config_save_is_atomic_and_leaves_no_temporary_file():
    with tempfile.TemporaryDirectory() as tmp:
        config = os.path.join(tmp, "config.json")
        with unittest.mock.patch.object(main, "CONFIG", config):
            main.save_cfg({"color": "black", "auto_color": True})
        with open(config, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["color"] == "black"
        assert not os.path.exists(config + ".tmp")


def test_packaged_apps_use_writable_platform_config_directories():
    assert main.default_config_path(
        "win32", frozen=True, environ={"APPDATA": "C:\\Data"},
        home="C:\\Users\\test") == os.path.join(
            "C:\\Data", "ChessHelper", "config.json")
    assert main.default_config_path(
        "darwin", frozen=True, environ={}, home="/Users/test") == os.path.join(
            "/Users/test", "Library", "Application Support",
            "ChessHelper", "config.json")


def test_stockfish_environment_override_is_cross_platform():
    with tempfile.TemporaryDirectory() as tmp:
        executable = os.path.join(tmp, "stockfish-custom")
        with open(executable, "wb") as f:
            f.write(b"test")
        with unittest.mock.patch.dict(
                os.environ, {"CHESSHELPER_STOCKFISH": executable}):
            assert main.stockfish_path() == executable


def test_macos_permission_check_is_noop_on_other_platforms():
    with unittest.mock.patch.object(main.sys, "platform", "win32"):
        assert main.macos_permissions_ready()


def test_side_shortcuts_are_native_on_windows_and_macos():
    assert main.side_shortcuts("win32") == (
        "<ctrl>+<alt>+b", "<ctrl>+<alt>+a",
        "Ctrl+Alt+B", "Ctrl+Alt+A")
    assert main.side_shortcuts("darwin") == (
        "<cmd>+<alt>+b", "<cmd>+<alt>+a",
        "Cmd+Option+B", "Cmd+Option+A")
    assert main.auto_move_shortcut("win32") == (
        "<ctrl>+<alt>+m", "Ctrl+Alt+M")
    assert main.auto_move_shortcut("darwin") == (
        "<cmd>+<alt>+m", "Cmd+Option+M")


def test_macos_permission_check_uses_native_authorization_apis():
    quartz = types.ModuleType("Quartz")
    quartz.CGPreflightScreenCaptureAccess = lambda: True
    quartz.CGRequestScreenCaptureAccess = lambda: False
    services = types.ModuleType("ApplicationServices")
    services.kAXTrustedCheckOptionPrompt = "prompt"
    services.AXIsProcessTrustedWithOptions = (
        lambda options: options == {"prompt": True})
    with (unittest.mock.patch.object(main.sys, "platform", "darwin"),
          unittest.mock.patch.dict(
              sys.modules,
              {"Quartz": quartz, "ApplicationServices": services})):
        assert main.macos_permissions_ready()


def test_tick_stops_when_overlay_would_contaminate_capture():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.overlay.capture_excluded = False
    assistant.cfg["rect"] = [100, 100, 900, 900]
    with (unittest.mock.patch.object(main.sys, "platform", "win32"),
          unittest.mock.patch.object(main.capture, "grab") as grab):
        assistant.tick()
    assert not grab.called
    flush_ui(assistant)
    assert "capture protection unavailable" in assistant.overlay.calls[-1][1]


def test_status_recovers_after_transient_unreadable_frame():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.waiting_for = "our_move"
    assistant.last_move_status = "Suggested move: e4 — follow the arrow"
    assistant.last_status = "Checking board — retrying automatically…"

    assistant._restore_status()
    flush_ui(assistant)

    assert assistant.overlay.calls[-1] == (
        "status", "Suggested move: e4 — follow the arrow")


def test_browser_safe_color_hotkey_disables_auto_detection():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.cfg["color"] = "white"
    assistant.cfg["auto_color"] = True
    with unittest.mock.patch.object(main, "save_cfg"):
        assistant.on_hotkey("switch_color")
    assert assistant.cfg["color"] == "black"
    assert assistant.cfg["auto_color"] is False

    flush_ui(assistant)
    assert ("controls", "black", False, False) in assistant.overlay.calls


def test_invalid_frame_breaks_stability_without_replacing_reference():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.templates = [{}]
    assistant.cfg["rect"] = [0, 0, 800, 800]
    assistant.cfg["auto_color"] = False
    assistant.stable_fen = chess.STARTING_FEN.split(" ")[0]
    assistant.stable_count = 2
    reference = np.ones((8, 8, 3), dtype=np.uint8)
    assistant.last_board_image = reference
    image = np.zeros((8, 8, 3), dtype=np.uint8)

    with (unittest.mock.patch.object(main.capture, "grab", return_value=image),
          unittest.mock.patch.object(main.bm, "grid_cells", return_value=[]),
          unittest.mock.patch.object(main.bm, "classify_grid", return_value=[]),
          unittest.mock.patch.object(main.bm, "cells_to_fen", return_value="bad"),
          unittest.mock.patch.object(main.bm, "valid_placement", return_value=False)):
        assistant.tick()

    assert assistant.stable_count == 0
    assert assistant.last_board_image is reference


def test_classifier_prefers_legal_history_over_lower_visual_error():
    assistant = Assistant()
    assistant.cfg["auto_color"] = False
    assistant.cfg["color"] = "white"
    assistant.position = chess.Board()
    assistant.prev_fen = assistant.position.board_fen()
    impossible = "rnbqkbnr/pppppppp/8/8/3PP3/8/PPP2PPP/RNBQKBNR"
    after_e4 = placement_after(["e2e4"])
    noisy_grid = object()
    legal_grid = object()

    def to_fen(grid, _color):
        return impossible if grid is noisy_grid else after_e4

    with (unittest.mock.patch.object(
              main.bm, "classify_grid_candidates",
              return_value=[
                  (False, 1.0, 0, noisy_grid),
                  (False, 10.0, 1, legal_grid),
              ]),
          unittest.mock.patch.object(
              main.bm, "cells_to_fen", side_effect=to_fen)):
        _orientation, fen_part, selected = assistant._classify_observation([])

    assert fen_part == after_e4
    assert selected is legal_grid
    assert assistant.template_index == 1


def test_assistant_recovers_three_missed_half_moves():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine()
    assistant.cfg["color"] = "white"
    assistant.cfg["rect"] = [100, 100, 900, 900]
    start = chess.STARTING_FEN.split(" ")[0]
    target = placement_after(["e2e4", "e7e5", "g1f3"])
    assistant.position = chess.Board()
    assistant.prev_fen = start
    assistant.waiting_for = "our_move"

    for _ in range(3):
        assistant.on_new_position(target)

    assert assistant.prev_fen == target
    assert assistant.position.board_fen() == target
    assert assistant.waiting_for == "opponent"
    assert not assistant.engine.calls


def test_relocation_recalculates_arrow_for_new_coordinates():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.engine = FakeEngine()
    assistant.templates = [{}]
    assistant.cfg["rect"] = [0, 0, 800, 800]
    assistant.cfg["color"] = "white"
    assistant.cfg["auto_color"] = True
    assistant.position = chess.Board()
    assistant.prev_fen = assistant.position.board_fen()
    assistant.waiting_for = "our_move"
    assistant.pending_move = chess.Move.from_uci("e2e4")
    assistant.last_board_image = np.zeros((800, 800, 3), dtype=np.uint8)
    candidate = [100, 100, 1000, 1000]
    image = np.zeros((900, 900, 3), dtype=np.uint8)

    with (unittest.mock.patch.object(
              main.capture, "grab_virtual_screen",
              return_value=(image, [0, 0, 1200, 1200])),
          unittest.mock.patch.object(
              main.capture, "find_relocated_rect", return_value=candidate),
          unittest.mock.patch.object(main.capture, "grab", return_value=image),
          unittest.mock.patch.object(main.bm, "grid_cells", return_value=[]),
          unittest.mock.patch.object(
              main.bm, "classify_grid_candidates",
              return_value=[(False, 0.0, 0, [])]),
          unittest.mock.patch.object(
              main.bm, "cells_to_fen", return_value=assistant.prev_fen),
          unittest.mock.patch.object(
              main.bm, "detect_orientation",
              return_value=("white", assistant.prev_fen)),
          unittest.mock.patch.object(main.bm, "valid_placement", return_value=True),
          unittest.mock.patch.object(main, "save_cfg")):
        assistant._try_relocate_board()

    assert assistant.cfg["rect"] == candidate
    assert assistant.waiting_for == "analysis"
    assert assistant.pending_move is None
    flush_ui(assistant)
    assert ("clear",) in assistant.overlay.calls
    assert ("board_rect", candidate) in assistant.overlay.calls


def test_failed_relocation_never_claims_the_board_moved():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.cfg["rect"] = [0, 0, 800, 800]
    assistant.last_board_image = np.zeros((800, 800, 3), dtype=np.uint8)
    desktop = np.zeros((900, 1200, 3), dtype=np.uint8)

    with (unittest.mock.patch.object(
              main.capture, "grab_virtual_screen",
              return_value=(desktop, [0, 0, 1200, 900])),
          unittest.mock.patch.object(
              main.capture, "find_relocated_rect", return_value=None)):
        assert assistant._try_relocate_board() is False

    flush_ui(assistant)
    statuses = [call[1] for call in assistant.overlay.calls
                if call[0] == "status"]
    assert not any("moved" in status.lower() for status in statuses)
    assert assistant.relocation_failures == 1


def test_relocation_requires_two_identical_legal_reads():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    original = [0, 0, 800, 800]
    candidate = [200, 100, 1000, 900]
    assistant.cfg["rect"] = original
    assistant.last_board_image = np.zeros((800, 800, 3), dtype=np.uint8)
    start = chess.STARTING_FEN.split(" ")[0]
    changed = placement_after(["e2e4"])
    image = np.zeros((800, 800, 3), dtype=np.uint8)

    with (unittest.mock.patch.object(
              main.capture, "grab_virtual_screen",
              return_value=(image, [0, 0, 1200, 900])),
          unittest.mock.patch.object(
              main.capture, "find_relocated_rect", return_value=candidate),
          unittest.mock.patch.object(main.capture, "grab", return_value=image),
          unittest.mock.patch.object(main.bm, "grid_cells", return_value=[]),
          unittest.mock.patch.object(
              assistant, "_classify_observation",
              side_effect=[("white", start, []), ("white", changed, [])]),
          unittest.mock.patch.object(main.time, "sleep"),
          unittest.mock.patch.object(main, "save_cfg") as save):
        assert assistant._try_relocate_board() is False

    assert assistant.cfg["rect"] == original
    assert not save.called
    assert assistant.relocation_failures == 1


def test_relocation_rejects_an_unrelated_valid_board():
    assistant = Assistant()
    assistant.cfg["color"] = "white"
    assistant.position = chess.Board()
    assistant.prev_fen = assistant.position.board_fen()
    unrelated = placement_after(["e2e4", "e7e5", "g1f3", "b8c6"])

    assert not assistant._relocation_position_plausible(unrelated)


def test_auto_move_clicks_source_then_destination():
    assistant = Assistant()
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["color"] = "white"
    assistant.cfg["auto_move"] = True
    move = chess.Move.from_uci("e2e4")
    assistant.pending_move = move
    assistant.waiting_for = "our_move"
    events = []

    class FakeMouse:
        @property
        def position(self):
            return None

        @position.setter
        def position(self, value):
            events.append(("position", value))

        def click(self, button):
            events.append(("click", button))

    with (unittest.mock.patch.object(
              main.mouse, "Controller", return_value=FakeMouse()),
          unittest.mock.patch.object(main.time, "sleep")):
        worker = assistant._start_auto_move(move)
        worker.join(timeout=2)

    assert events == [
        ("position", bm.square_center(chess.E2, assistant.cfg["rect"], "white")),
        ("click", main.mouse.Button.left),
        ("position", bm.square_center(chess.E4, assistant.cfg["rect"], "white")),
        ("click", main.mouse.Button.left),
    ]


def test_auto_move_never_clicks_promotions():
    assistant = Assistant()
    assistant.overlay = FakeOverlay()
    assistant.cfg["rect"] = [100, 200, 900, 1000]
    assistant.cfg["auto_move"] = True
    move = chess.Move.from_uci("a7a8q")
    assistant.pending_move = move
    assistant.waiting_for = "our_move"
    with unittest.mock.patch.object(main.mouse, "Controller") as controller:
        assert assistant._start_auto_move(move) is None
    assert not controller.called


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for fn in (
        test_start_position_queues_white_move_arrow,
        test_arrow_changes_only_after_our_move_then_opponent_move,
        test_impossible_position_does_not_replace_current_arrow,
        test_new_starting_board_resets_late_game_tracking,
        test_state_preserves_en_passant_history,
        test_midgame_attachment_does_not_invent_castling_rights,
        test_failed_analysis_remains_pending_and_can_retry_same_position,
        test_float_config_rect_is_normalized_for_screen_capture,
        test_invalid_saved_calibration_is_discarded,
        test_auto_move_is_always_off_at_application_start,
        test_config_save_is_atomic_and_leaves_no_temporary_file,
        test_packaged_apps_use_writable_platform_config_directories,
        test_stockfish_environment_override_is_cross_platform,
        test_macos_permission_check_is_noop_on_other_platforms,
        test_side_shortcuts_are_native_on_windows_and_macos,
        test_macos_permission_check_uses_native_authorization_apis,
        test_tick_stops_when_overlay_would_contaminate_capture,
        test_status_recovers_after_transient_unreadable_frame,
        test_browser_safe_color_hotkey_disables_auto_detection,
        test_invalid_frame_breaks_stability_without_replacing_reference,
        test_classifier_prefers_legal_history_over_lower_visual_error,
        test_assistant_recovers_three_missed_half_moves,
        test_relocation_recalculates_arrow_for_new_coordinates,
        test_failed_relocation_never_claims_the_board_moved,
        test_relocation_requires_two_identical_legal_reads,
        test_relocation_rejects_an_unrelated_valid_board,
        test_auto_move_clicks_source_then_destination,
        test_auto_move_never_clicks_promotions,
    ):
        suite.addTest(unittest.FunctionTestCase(fn))
    return suite


if __name__ == "__main__":
    test_start_position_queues_white_move_arrow()
    test_arrow_changes_only_after_our_move_then_opponent_move()
    test_impossible_position_does_not_replace_current_arrow()
    test_new_starting_board_resets_late_game_tracking()
    test_state_preserves_en_passant_history()
    test_midgame_attachment_does_not_invent_castling_rights()
    test_failed_analysis_remains_pending_and_can_retry_same_position()
    test_float_config_rect_is_normalized_for_screen_capture()
    test_invalid_saved_calibration_is_discarded()
    test_stockfish_environment_override_is_cross_platform()
    test_macos_permission_check_is_noop_on_other_platforms()
    test_macos_permission_check_uses_native_authorization_apis()
    test_tick_stops_when_overlay_would_contaminate_capture()
    print("assistant start-position arrow test: PASS")
