# ChessHelper

Real-time move assistant for chess.com/play. Captures the board off the screen,
finds the best move with Stockfish, and draws an arrow over your board showing
where to move.

## Install

1. `python -m venv .venv`
2. `.venv\Scripts\pip install -r requirements.txt`
3. `.venv\Scripts\python setup.py`  (downloads Stockfish + chess.com piece images)

Or just double-click `start.bat` — it creates the venv, installs everything, and
runs setup on first launch.

## Usage

1. Open a game on https://www.chess.com/play.
2. On first launch, calibration opens automatically (press F10 later to redo
   it). The live desktop dims slightly. Click the TOP-LEFT OUTER EDGE
   of the 8x8 board, then the BOTTOM-RIGHT OUTER EDGE. Check the green preview
   lines: they should fall on every square boundary. Click the **SAVE GRID**
   button to finish; click elsewhere to redo; press Esc or right-click to cancel.
3. A status message appears at the top of the screen. Wait for “Your turn” and
   the arrow to appear over the board. The label shows the move and evaluation.

## Hotkeys

| Key  | Action                        |
|------|-------------------------------|
| F9   | pause / resume                |
| F10  | calibrate board               |
| F11  | switch your color (white/black) |
| F8   | quit                          |

## How it works

- The calibrated screen region is captured ~6x/sec with mss.
- Each square is classified against chess.com's own supported piece images
  with OpenCV template matching.
- The position becomes a FEN; move detection keeps legal game history so
  castling rights, en passant, and turn order survive across screenshots.
- Board orientation and move notation are normalized for both White and Black.
- Stockfish searches for your side; the best move is drawn on a transparent,
  click-through overlay. You move the piece yourself.

## Notes

- Keep the browser window in place after calibrating; press F10 again if it
  moves or resizes.
- Keep the entire board visible and uncovered. ChessHelper reads visible screen
  pixels, so another window over the board prevents recognition.
- Restart `start.bat` after updating the project so the new calibration window
  is loaded.
- Neo, classic, neon, and the current cyber computer-event piece sets work out
  of the box. Other sets need
  their images added under `templates/`.
- Browser zoom 100%. Calibration and arrows support the full multi-monitor
  desktop, including monitors left of the primary display.
- Calibrate the 64-square board area only; exclude coordinates, clocks, borders,
  and margins. The green preview must line up with all 8 rows and columns.
- Calibration rejects rectangles smaller than 160 px or more than 20% away
  from square proportions; click the board's actual 8x8 boundary.
- If no arrow appears, read the status text at the top of the screen. It tells
  you whether calibration is missing, the board is not recognized, or it is
  waiting for the opponent's turn.
- The overlay is click-through and invisible to the game page; all clicks land
  on chess.com as normal.
