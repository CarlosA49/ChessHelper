"""Offline self-test: Stockfish + synthetic-board vision round trip."""
import os
import shutil
import sys

import chess
import cv2
import numpy as np

import board as bm
import engine as eng

BASE = os.path.dirname(os.path.abspath(__file__))

LIGHT = (235, 236, 208)  # BGR chess.com green board light square (#EBECD0)
DARK = (119, 149, 86)    # dark square (#779556)


def make_board_image(fen_part, orientation="white"):
    """Render a position using the downloaded piece set, like a real screenshot."""
    sets = bm.load_template_sets()
    if not sets:
        print("FAIL: no template sets - run setup.py first")
        sys.exit(1)
    template_set = sets[0]
    cell = 90
    img = np.zeros((8 * cell, 8 * cell, 3), np.uint8)
    for r in range(8):
        for c in range(8):
            color = LIGHT if (r + c) % 2 == 0 else DARK
            img[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = color
    b = chess.Board(fen=f"{fen_part} w - - 0 1")
    for sq in chess.SQUARES:
        p = b.piece_at(sq)
        if p is None:
            continue
        if orientation == "white":
            r = 7 - chess.square_rank(sq)
            c = chess.square_file(sq)
        else:
            r = chess.square_rank(sq)
            c = 7 - chess.square_file(sq)
        key = ("w" if p.color else "b", p.symbol().lower())
        tpl_bgr = template_set[key]["bgra"]
        resized = cv2.resize(tpl_bgr, (cell, cell))
        alpha = resized[:, :, 3:4].astype(np.float32) / 255.0
        region = img[
            r * cell:(r + 1) * cell,
            c * cell:(c + 1) * cell,
        ].astype(np.float32)
        blended = resized[:, :, :3].astype(np.float32) * alpha + region * (1 - alpha)
        img[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = blended.astype(np.uint8)
    return img


def test_fen(fen_part, color="white"):
    img = make_board_image(fen_part, color)
    cells = bm.grid_cells(img)
    grid = bm.classify_grid(cells, bm.load_template_sets())
    got = bm.cells_to_fen(grid, color)
    ok = got == fen_part
    print(f"[{'PASS' if ok else 'FAIL'}] vision: {got}")
    if not ok:
        print(f"        expected: {fen_part}")
    return ok


def test_moved():
    prev = bm.make_board(chess.STARTING_FEN.split(" ")[0], "w")
    new = bm.make_board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR", "b")
    ok = bm.made_move(prev, new)
    print(f"[{'PASS' if ok else 'FAIL'}] move detection: 1.e4 detected as white's move")
    return ok


def test_black_orientation():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    b = chess.Board(fen=f"{fen} w - - 0 1")
    cells = [[None for _ in range(8)] for _ in range(8)]
    for sq, piece in b.piece_map().items():
        row = chess.square_rank(sq)
        col = 7 - chess.square_file(sq)
        cells[row][col] = ("w" if piece.color else "b", piece.symbol().lower())
    got = bm.cells_to_fen(cells, "black")
    ok = got == fen
    print(f"[{'PASS' if ok else 'FAIL'}] black orientation: {got}")
    return ok


def test_engine():
    local = os.path.join(
        BASE, "stockfish", "stockfish.exe" if sys.platform == "win32" else "stockfish")
    exe = (os.environ.get("CHESSHELPER_STOCKFISH")
           or (local if os.path.isfile(local) else None)
           or shutil.which("stockfish")
           or next((path for path in (
               "/opt/homebrew/bin/stockfish",
               "/usr/local/bin/stockfish",
           ) if os.path.isfile(path)), None))
    if not exe:
        print("[SKIP] stockfish missing - run setup.py")
        return True
    e = eng.Engine(exe)
    best, cp, _mate = e.analyze(chess.STARTING_FEN, 200)
    # A browser/game session can outlive a crashed engine process. Verify the
    # wrapper recreates Stockfish instead of leaving the overlay at Error 22.
    e.proc.terminate()
    e.proc.wait(timeout=2)
    recovered, _, _ = e.analyze(chess.STARTING_FEN, 100)
    e.quit()
    legal = {move.uci() for move in chess.Board().legal_moves}
    ok = best in legal and recovered in legal
    print(f"[{'PASS' if ok else 'FAIL'}] engine startpos/restart: "
          f"{best} -> {recovered} ({cp} cp)")
    return ok


def main():
    results = [
        test_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"),
        test_fen("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR"),
        test_fen("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R"),
        test_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", "black"),
        test_moved(),
        test_black_orientation(),
        test_engine(),
    ]
    print("selftest:", "ALL PASS" if all(results) else "SOME FAILED")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
