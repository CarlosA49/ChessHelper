"""Regression tests for board orientation, geometry, and move matching."""
import unittest

import chess
import cv2
import numpy as np

import board as bm
import capture
from calibrate import grid_lines, normalize_rect, window_geometry
from overlay import control_rows, move_label_position, screen_to_local
from test_assistant import test_start_position_queues_white_move_arrow

START = chess.STARTING_FEN.split(" ")[0]


def screen_grid_from_fen(fen_part, color):
    """Make a symbolic screen grid from a FEN for the requested orientation."""
    b = chess.Board(fen=f"{fen_part} w - - 0 1")
    grid = [[None for _ in range(8)] for _ in range(8)]
    for sq, piece in b.piece_map().items():
        file_i = chess.square_file(sq)
        rank_i = chess.square_rank(sq)
        if color == "white":
            row, col = 7 - rank_i, file_i
        else:
            row, col = rank_i, 7 - file_i
        grid[row][col] = ("w" if piece.color else "b", piece.symbol().lower())
    return grid


class OrientationTests(unittest.TestCase):
    def test_white_and_black_screen_orientation_encode_same_fen(self):
        for fen in (START,
                    "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R"):
            with self.subTest(fen=fen):
                white_screen = screen_grid_from_fen(fen, "white")
                black_screen = screen_grid_from_fen(fen, "black")
                self.assertEqual(bm.cells_to_fen(white_screen, "white"), fen)
                self.assertEqual(bm.cells_to_fen(black_screen, "black"), fen)

    def test_black_screen_square_centers(self):
        rect = [100, 200, 900, 1000]
        self.assertEqual(bm.square_center(chess.H1, rect, "black"), (150, 250))
        self.assertEqual(bm.square_center(chess.A8, rect, "black"), (850, 950))

    def test_orientation_is_detected_from_either_screen_direction(self):
        for actual in ("white", "black"):
            with self.subTest(actual=actual):
                grid = screen_grid_from_fen(START, actual)
                detected, fen = bm.detect_orientation(grid)
                self.assertEqual(detected, actual)
                self.assertEqual(fen, START)

    def test_sparse_endgame_keeps_preferred_orientation(self):
        sparse = "8/8/2k5/8/8/5K2/8/8"
        grid = screen_grid_from_fen(sparse, "white")
        for preferred in ("white", "black"):
            with self.subTest(preferred=preferred):
                detected, _fen = bm.detect_orientation(grid, preferred)
                self.assertEqual(detected, preferred)

    def test_every_move_endpoint_stays_inside_calibrated_board(self):
        rect = [100, 200, 900, 1000]
        for color in ("white", "black"):
            for square in chess.SQUARES:
                with self.subTest(color=color, square=chess.square_name(square)):
                    x, y = bm.square_center(square, rect, color)
                    self.assertGreater(rect[2], x)
                    self.assertLess(rect[0], x)
                    self.assertGreater(rect[3], y)
                    self.assertLess(rect[1], y)


class CalibrationTests(unittest.TestCase):
    def test_relocation_ignores_template_matching_jitter(self):
        original = [100, 200, 900, 1000]
        self.assertTrue(capture.rects_are_near(
            original, [106, 194, 906, 994]))
        self.assertFalse(capture.rects_are_near(
            original, [125, 200, 925, 1000]))

    def test_control_guide_uses_native_mac_labels(self):
        keys = [key for key, _action in control_rows("darwin")]
        self.assertIn("Cmd+Option+B", keys)
        self.assertIn("Cmd+Option+A", keys)
        self.assertIn("Cmd+Option+M", keys)
        self.assertNotIn("Ctrl+Alt+B", keys)

    def test_coregraphics_bgra_conversion_removes_row_padding(self):
        raw = bytes([
            1, 2, 3, 255, 4, 5, 6, 255, 99, 99, 99, 99,
            7, 8, 9, 255, 10, 11, 12, 255, 88, 88, 88, 88,
        ])
        image = capture._bytes_to_bgr(raw, width=2, height=2, bytes_per_row=12)
        np.testing.assert_array_equal(image, np.array([
            [[1, 2, 3], [4, 5, 6]],
            [[7, 8, 9], [10, 11, 12]],
        ], dtype=np.uint8))

    def test_screen_capture_session_is_reused_and_closed_per_thread(self):
        class FakeSession:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        capture.close_thread_capture()
        session = FakeSession()
        with unittest.mock.patch.object(
                capture.mss, "mss", return_value=session) as constructor:
            self.assertIs(capture._screen_capture(), session)
            self.assertIs(capture._screen_capture(), session)
        self.assertEqual(constructor.call_count, 1)
        capture.close_thread_capture()
        self.assertTrue(session.closed)

    def test_saved_board_image_can_relocate_capture_automatically(self):
        reference = np.zeros((240, 240, 3), dtype=np.uint8)
        for row in range(8):
            for col in range(8):
                color = (55, 90, 120) if (row + col) % 2 else (180, 205, 220)
                reference[row * 30:(row + 1) * 30,
                          col * 30:(col + 1) * 30] = color
        desktop = np.zeros((600, 900, 3), dtype=np.uint8)
        moved = cv2.resize(reference, (270, 270), interpolation=cv2.INTER_AREA)
        desktop[170:440, 410:680] = moved

        rect = capture.find_relocated_rect(
            reference, desktop, [-100, 50, 800, 650])

        self.assertIsNotNone(rect)
        self.assertLessEqual(abs(rect[0] - 310), 3)
        self.assertLessEqual(abs(rect[1] - 220), 3)
        self.assertLessEqual(abs(rect[2] - 580), 3)
        self.assertLessEqual(abs(rect[3] - 490), 3)

    def test_corner_order_is_normalized(self):
        self.assertEqual(normalize_rect([(900, 900), (100, 100)]),
                         [100, 100, 900, 900])

    def test_bad_size_and_aspect_are_rejected(self):
        self.assertIsNone(normalize_rect([(0, 0), (100, 100)]))
        self.assertIsNone(normalize_rect([(0, 0), (800, 300)]))

    def test_preview_grid_has_nine_even_boundaries(self):
        xs, ys = grid_lines([100, 200, 900, 1000])
        self.assertEqual(len(xs), 9)
        self.assertEqual(len(ys), 9)
        self.assertEqual((xs[0], xs[-1]), (100, 900))
        self.assertEqual((ys[0], ys[-1]), (200, 1000))

    def test_arrow_screen_coordinates_convert_to_canvas_coordinates(self):
        self.assertEqual(screen_to_local(700, 650, (100, 50)), (600, 600))

    def test_virtual_desktop_geometry_supports_negative_monitor_coordinates(self):
        self.assertEqual(window_geometry(3840, 1080, -1920, 0),
                         "3840x1080-1920+0")

    def test_move_label_stays_above_board_and_off_destination(self):
        board = [300, 220, 940, 860]
        target = (900, 820)
        x, y = move_label_position(target, board, (0, 0), (1920, 1080))
        self.assertLess(y + 19, board[1])
        self.assertGreater(abs(x - target[0]) + abs(y - target[1]), 100)

    def test_move_label_uses_side_when_board_touches_top(self):
        board = [100, 0, 740, 640]
        x, _y = move_label_position((700, 40), board, (0, 0), (1920, 1080))
        self.assertGreater(x - 76, board[2])


class MoveDetectionTests(unittest.TestCase):
    def test_invalid_but_count_plausible_placements_are_rejected(self):
        invalid = [
            "4k3/8/8/8/8/8/4K3/P7",       # pawn on rank 1
            "8/8/8/8/8/8/4k3/4K3",        # adjacent kings
            "4k3/8/8/8/8/NNNBBRRQ/PPPPPPPP/4K3",  # 17 white pieces
        ]
        for fen in invalid:
            with self.subTest(fen=fen):
                self.assertFalse(bm.valid_placement(fen))

    def test_move_recognition_across_capture_and_promotion(self):
        cases = [
            ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", "w",
             "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"),
            ("4k3/P7/8/8/8/8/8/4K3", "w", "Q3k3/8/8/8/8/8/8/4K3"),
        ]
        for before, turn, after in cases:
            with self.subTest(before=before):
                self.assertTrue(bm.made_move(bm.make_board(before, turn),
                                             bm.make_board(after, turn)))

    def test_start_position_produces_arrow(self):
        test_start_position_queues_white_move_arrow()

    def test_castling_move_is_detected(self):
        before = "r3k2r/8/8/8/8/8/8/R3K2R"
        after = "r3k2r/8/8/8/8/8/8/R4RK1"
        self.assertTrue(bm.made_move(bm.make_board(before, "w"),
                                     bm.make_board(after, "w")))

    def test_three_missed_half_moves_can_be_resynchronized(self):
        tracked = chess.Board()
        target = chess.Board()
        expected = ["e2e4", "e7e5", "g1f3"]
        for move in expected:
            target.push_uci(move)

        sequence = bm.find_move_sequence(
            tracked, target.piece_map(), max_plies=3)

        self.assertEqual(len(sequence), 3)
        recovered = tracked.copy()
        for move in sequence:
            recovered.push(move)
        self.assertEqual(recovered.piece_map(), target.piece_map())
        self.assertEqual(tracked, chess.Board())


if __name__ == "__main__":
    unittest.main()
