"""Board vision: screenshots -> chess positions (FEN)."""
import os

import chess
import cv2
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE, "templates")

CANON = 56           # canonical shape size for mask matching
DIST_THRESH = 45     # pixel distance from square background that counts as piece
MIN_MASK_FRAC = 0.03 # below this fraction of the cell, the square is empty
MIN_TOTAL = 0.42     # min combined shape+brightness score to accept a piece
FULL_CELL_MIN_MASK_FRAC = 0.10
PIECES = "pnbrqk"


def _bbox(mask):
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()


def _trim_resize(img, bbox, size):
    x1, y1, x2, y2 = bbox
    crop = img[y1:y2 + 1, x1:x2 + 1]
    return cv2.resize(crop, size, interpolation=cv2.INTER_AREA)


def load_template_sets():
    """Load every complete piece set in templates/<set>/<color><piece>.png.
    Each template is preprocessed to canonical shape/brightness arrays."""
    sets = []
    if not os.path.isdir(TEMPLATES_DIR):
        return sets
    for set_name in sorted(os.listdir(TEMPLATES_DIR)):
        path = os.path.join(TEMPLATES_DIR, set_name)
        if not os.path.isdir(path):
            continue
        t = {}
        ok = True
        for color in "wb":
            for p in PIECES:
                fp = os.path.join(path, f"{color}{p}.png")
                if not os.path.isfile(fp):
                    ok = False
                    break
                bgra = cv2.imread(fp, cv2.IMREAD_UNCHANGED)
                if bgra is None or bgra.ndim != 3 or bgra.shape[2] != 4:
                    ok = False
                    break
                alpha = bgra[:, :, 3] > 10
                bb = _bbox(alpha)
                if bb is None:
                    ok = False
                    break
                gray = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
                gray_trimmed = _trim_resize(gray, bb, (CANON, CANON))
                mask_trimmed = _trim_resize(
                    alpha.astype(np.float32), bb, (CANON, CANON))
                t[(color, p)] = {
                    "mask": mask_trimmed,
                    "gray": gray_trimmed,
                    "mean": float(gray[alpha].mean()),
                    "bgra": bgra,
                }
            if not ok:
                break
        if ok:
            sets.append(t)
    return sets


def grid_cells(img):
    """Split a board screenshot into exact, full 8x8 squares.

    Keeping the complete square is important for Chess.com's piece assets:
    they are already aligned to a square, and cropping shifts edge pieces and
    makes rooks, bishops, pawns, and knights look deceptively similar.
    """
    h, w = img.shape[:2]
    cells = []
    for r in range(8):
        row = []
        for c in range(8):
            y1 = round(r * h / 8.0)
            y2 = round((r + 1) * h / 8.0)
            x1 = round(c * w / 8.0)
            x2 = round((c + 1) * w / 8.0)
            row.append(img[y1:y2, x1:x2])
        cells.append(row)
    return cells


def cell_mask(cell):
    """Binary piece mask + background color estimate for a square cell."""
    h, w = cell.shape[:2]
    n = max(2, min(h, w) // 9)
    corners = np.concatenate([
        cell[:n, :n].reshape(-1, 3),
        cell[:n, -n:].reshape(-1, 3),
        cell[-n:, :n].reshape(-1, 3),
        cell[-n:, -n:].reshape(-1, 3),
    ]).astype(np.float32)
    bg = np.median(corners, axis=0)
    dist = np.linalg.norm(cell.astype(np.float32) - bg, axis=2)
    return dist > DIST_THRESH, bg


def classify_cell(cell, sets):
    """Return (color, piece) or None for an empty square.

    The cell's piece mask is trimmed to its bounding box and resized to a
    canonical size, then matched against every template's canonical shape
    and brightness. Shape matching decides the piece type; brightness
    matching decides white vs black."""
    mask, _ = cell_mask(cell)
    if mask.mean() < MIN_MASK_FRAC:
        return None
    bb = _bbox(mask)
    if bb is None:
        return None
    h, w = cell.shape[:2]
    bw, bh = bb[2] - bb[0] + 1, bb[3] - bb[1] + 1
    if bw < 0.12 * w or bh < 0.12 * h:
        return None  # too small to be a piece (highlight noise, etc.)
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    m_canon = _trim_resize(mask.astype(np.float32), bb, (CANON, CANON))
    g_canon = _trim_resize(gray, bb, (CANON, CANON))
    # Template PNGs have transparent pixels represented as black when read in
    # grayscale. Compare only foreground pixels so light/dark board squares,
    # textured themes, and last-move highlights cannot overwhelm an otherwise
    # exact piece-shape match.
    g_canon = g_canon.copy()
    g_canon[m_canon < 0.5] = 0
    foreground_mean = float(gray[mask].mean())
    best = None
    best_total = MIN_TOTAL
    for t in sets:
        for (color, p), meta in t.items():
            ms = cv2.matchTemplate(m_canon, meta["mask"], cv2.TM_CCOEFF_NORMED)[0][0]
            gs = cv2.matchTemplate(g_canon, meta["gray"], cv2.TM_CCOEFF_NORMED)[0][0]
            # Mean foreground brightness is a strong color cue for stylized
            # sets whose white/black pieces have nearly identical silhouettes.
            bs = 1.0 - min(1.0, abs(foreground_mean - meta["mean"]) / 255.0)
            total = 0.45 * ms + 0.20 * gs + 0.35 * bs
            if total > best_total:
                best_total = total
                best = (color, p)
    return best


def classify_grid(cells, sets):
    """Classify a position with one consistent piece theme.

    Chess.com piece PNGs occupy a complete square.  Composite each official
    PNG over the observed square color and compare the aligned pixels.  The
    old implementation cropped every piece to its silhouette and freely mixed
    themes, which caused systematic errors on the outer files.
    """
    candidates = []
    for template_set in sets:
        grid = []
        errors = []
        for row in cells:
            out_row = []
            for cell in row:
                piece, error = _classify_aligned(cell, template_set)
                out_row.append(piece)
                if error is not None:
                    errors.append(error)
            grid.append(out_row)
        average_error = float(np.mean(errors)) if errors else float("inf")
        fen_part = cells_to_fen(grid, "white")
        candidates.append((not valid_placement(fen_part), average_error, grid))

    if not candidates:
        return [[None for _ in row] for row in cells]
    # Prefer a structurally plausible board, then the lowest whole-board error.
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _classify_aligned(cell, template_set):
    """Return ((color, piece), MAE), or (None, None) for an empty square."""
    mask, bg = cell_mask(cell)
    if mask.mean() < FULL_CELL_MIN_MASK_FRAC:
        return None, None

    h, w = cell.shape[:2]
    observed = cell.astype(np.float32)
    best_piece = None
    best_error = float("inf")
    for key, meta in template_set.items():
        template = cv2.resize(meta["bgra"], (w, h), interpolation=cv2.INTER_AREA)
        alpha = template[:, :, 3:4].astype(np.float32) / 255.0
        rendered = (template[:, :, :3].astype(np.float32) * alpha
                    + bg.reshape(1, 1, 3) * (1.0 - alpha))
        error = float(np.abs(observed - rendered).mean())
        if error < best_error:
            best_error = error
            best_piece = key
    return best_piece, best_error


def cells_to_fen(cells, color):
    """cells: 8x8 grid of (color, piece)|None, row 0 = top of screen.
    color: 'white'|'black' — the player's own side (board orientation)."""
    ranks = []
    for r in range(8):
        parts = []
        empty = 0
        for c in range(8):
            # FEN is always rank 8 to rank 1, file a to h. A black-side
            # screen shows rank 1 at the top and file h on the left.
            screen_r = r if color == "white" else 7 - r
            screen_c = c if color == "white" else 7 - c
            cell = cells[screen_r][screen_c]
            if cell is None:
                empty += 1
            else:
                if empty:
                    parts.append(str(empty))
                    empty = 0
                pc, pp = cell
                parts.append(pp.upper() if pc == "w" else pp)
        if empty:
            parts.append(str(empty))
        ranks.append("".join(parts))
    return "/".join(ranks)


def valid_placement(fen_part):
    """Sanity check: 8 ranks, 8 files each, one king per side, plausible counts."""
    try:
        ranks = fen_part.split("/")
        if len(ranks) != 8:
            return False
        counts = {}
        for rank in ranks:
            col = 0
            for ch in rank:
                if ch in "12345678":
                    col += int(ch)
                else:
                    if ch not in "pnbrqkPNBRQK":
                        return False
                    counts[ch] = counts.get(ch, 0) + 1
                    col += 1
            if col != 8:
                return False
        if counts.get("k", 0) != 1 or counts.get("K", 0) != 1:
            return False
        if any(ch in "pP" for ch in ranks[0] + ranks[-1]):
            return False
        for ch, limit in (("p", 8), ("P", 8), ("n", 10), ("N", 10),
                          ("b", 10), ("B", 10), ("r", 10), ("R", 10),
                          ("q", 9), ("Q", 9)):
            if counts.get(ch, 0) > limit:
                return False
        if (sum(counts.get(ch, 0) for ch in "pnbrqk") > 16
                or sum(counts.get(ch, 0) for ch in "PNBRQK") > 16):
            return False
        position = chess.Board(f"{fen_part} w - - 0 1")
        white_king = position.king(chess.WHITE)
        black_king = position.king(chess.BLACK)
        if chess.square_distance(white_king, black_king) <= 1:
            return False
        if (position.is_attacked_by(chess.BLACK, white_king)
                and position.is_attacked_by(chess.WHITE, black_king)):
            return False
        return True
    except Exception:
        return False


def infer_rights(b):
    """Guess castling rights from king/rook placement (screen can't show history)."""
    rights = 0
    if b.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE):
        if b.piece_at(chess.H1) == chess.Piece(chess.ROOK, chess.WHITE):
            rights |= chess.BB_H1
        if b.piece_at(chess.A1) == chess.Piece(chess.ROOK, chess.WHITE):
            rights |= chess.BB_A1
    if b.piece_at(chess.E8) == chess.Piece(chess.KING, chess.BLACK):
        if b.piece_at(chess.H8) == chess.Piece(chess.ROOK, chess.BLACK):
            rights |= chess.BB_H8
        if b.piece_at(chess.A8) == chess.Piece(chess.ROOK, chess.BLACK):
            rights |= chess.BB_A8
    return rights


def make_board(fen_part, turn):
    b = chess.Board(fen=f"{fen_part} {turn} - - 0 1")
    b.castling_rights = infer_rights(b)
    return b


def fen_with(fen_part, turn):
    b = make_board(fen_part, turn)
    return f"{fen_part} {turn} {b.castling_xfen()} 0 1"


def made_move(prev, new):
    """True if some legal move of prev transforms its placement into new's."""
    return find_move(prev, new.piece_map()) is not None


def find_move(board, target_map):
    """Find the legal move that reaches target_map without losing history."""
    for move in list(board.legal_moves):
        board.push(move)
        matches = board.piece_map() == target_map
        board.pop()
        if matches:
            return move
    return None


def find_two_moves(board, target_map):
    """Find two consecutive legal moves reaching target_map, if any."""
    for first in list(board.legal_moves):
        board.push(first)
        for second in list(board.legal_moves):
            board.push(second)
            matches = board.piece_map() == target_map
            board.pop()
            if matches:
                board.pop()
                return first, second
        board.pop()
    return None


def square_center(sq, rect, color):
    """Map a chess square to the pixel center of its cell on screen."""
    f = chess.square_file(sq)
    r = chess.square_rank(sq)
    if color == "white":
        scr_r, scr_c = 7 - r, f
    else:
        scr_r, scr_c = r, 7 - f
    x = rect[0] + (scr_c + 0.5) * (rect[2] - rect[0]) / 8.0
    y = rect[1] + (scr_r + 0.5) * (rect[3] - rect[1]) / 8.0
    return int(x), int(y)

