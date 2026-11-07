"""Build a ChessHelper template set from a calibrated board screenshot.

Useful for chess.com event/bot themes whose assets are not exposed under the
normal named piece-set URLs. The reference FEN tells the tool which visible
piece occupies each square; the current position must contain all 12 kinds.
"""
import argparse
import os

import chess
import cv2
import numpy as np

import board as bm


def screen_cell(square, orientation):
    file_i = chess.square_file(square)
    rank_i = chess.square_rank(square)
    if orientation == "white":
        return 7 - rank_i, file_i
    return rank_i, 7 - file_i


def capture_set(image, fen_part, orientation, output_dir):
    position = chess.Board(f"{fen_part} w - - 0 1")
    cells = bm.grid_cells(image)
    os.makedirs(output_dir, exist_ok=True)
    written = set()

    for square, piece in position.piece_map().items():
        key = ("w" if piece.color else "b") + piece.symbol().lower()
        if key in written:
            continue
        row, col = screen_cell(square, orientation)
        cell = cells[row][col]
        mask, _ = bm.cell_mask(cell)
        if mask.mean() < bm.MIN_MASK_FRAC:
            continue

        # Store the observed piece pixels with a transparent background. The
        # normal loader then applies the same crop/resize path as downloaded
        # chess.com templates.
        bgra = cv2.cvtColor(cell, cv2.COLOR_BGR2BGRA)
        bgra[:, :, :3][~mask] = 0
        bgra[:, :, 3] = mask.astype(np.uint8) * 255
        cv2.imwrite(os.path.join(output_dir, key + ".png"), bgra)
        written.add(key)

    expected = {color + piece for color in "wb" for piece in bm.PIECES}
    missing = sorted(expected - written)
    if missing:
        raise RuntimeError(
            "reference position is missing templates: " + ", ".join(missing))
    return sorted(written)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("fen_part")
    parser.add_argument("--orientation", choices=("white", "black"), default="white")
    parser.add_argument("--name", default="captured")
    args = parser.parse_args()

    image = cv2.imread(args.image)
    if image is None:
        raise SystemExit(f"cannot read image: {args.image}")
    output = os.path.join(bm.TEMPLATES_DIR, args.name)
    written = capture_set(image, args.fen_part, args.orientation, output)
    print(f"captured {len(written)} templates in {output}")


if __name__ == "__main__":
    main()
