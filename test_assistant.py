"""Integration tests for turn-to-arrow behavior without a desktop game."""
import json
import os
import sys
import tempfile
import types
import unittest
import unittest.mock

import chess

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

    def show_arrow(self, x1, y1, x2, y2, label):
        self.calls.append(("arrow", x1, y1, x2, y2, label))


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
        test_stockfish_environment_override_is_cross_platform,
        test_macos_permission_check_is_noop_on_other_platforms,
        test_macos_permission_check_uses_native_authorization_apis,
        test_tick_stops_when_overlay_would_contaminate_capture,
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
