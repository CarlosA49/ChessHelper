# ChessHelper

Real-time move assistant for chess.com/play. Captures the board off the screen,
finds the best move with Stockfish, and draws an arrow over your board showing
where to move.

## Private release packages

The repository's private **Releases** page provides standalone Windows x64,
macOS Apple Silicon, and macOS Intel packages. Extract the ZIP before running.
On macOS, right-click `ChessHelper.app` and choose **Open** on first launch if
Gatekeeper asks for confirmation. Packaged settings are stored in the user's
application-data directory rather than inside the application bundle.

## Install on Windows

1. `python -m venv .venv`
2. `.venv\Scripts\pip install -r requirements.txt`
3. `.venv\Scripts\python setup.py`  (downloads Stockfish + chess.com piece images)

Or just double-click `start.bat` — it creates the venv, installs everything, and
runs setup on first launch.

## Install on macOS

1. Install [Homebrew](https://brew.sh) and Python 3 from
   [python.org](https://www.python.org/downloads/macos/) if they are not already
   installed.
2. Double-click `start.command`. It creates the virtual environment, installs
   the Python packages and Stockfish, downloads the piece templates, and starts
   ChessHelper.
3. On first launch, allow both **Screen Recording** and **Accessibility** in
   **System Settings → Privacy & Security**. Close the Terminal window and
   double-click `start.command` again after granting them.

If macOS refuses to open the launcher after downloading the repository, open
Terminal in the project folder and run `chmod +x start.command`, then run it
again. Apple Silicon and Intel Homebrew paths are both detected automatically.

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
| F7   | hide / show the on-screen control guide |
| F9   | pause / resume                |
| F10  | calibrate board               |
| Ctrl+Alt+B | manually switch color on Windows/Linux |
| Ctrl+Alt+A | restore automatic color detection on Windows/Linux |
| Cmd+Option+B | manually switch color on macOS |
| Cmd+Option+A | restore automatic color detection on macOS |
| Ctrl+Alt+M | toggle automatic moves on Windows/Linux |
| Cmd+Option+M | toggle automatic moves on macOS |
| F8   | quit                          |

The overlay displays this guide in the upper-right corner together with the
current side and `AUTO` or `MANUAL` orientation mode.

Automatic moves are disabled at every launch and require an explicit opt-in
for the current session. When enabled, ChessHelper clicks the suggested source
and destination squares after analysis. Promotions remain manual so the
desired promotion piece can be selected safely. Use automated play only on
boards and game modes whose rules permit it.

On a Mac keyboard configured to use media controls, hold `fn` while pressing
the function-key shortcuts. The on-screen guide automatically shows the native
Mac `Cmd+Option` combinations.

## How it works

- The calibrated screen region is captured ~6x/sec with mss.
- Each square is classified against chess.com's own supported piece images
  with OpenCV template matching.
- The position becomes a FEN; move detection keeps legal game history so
  castling rights, en passant, and turn order survive across screenshots.
- Board orientation and your side are detected automatically at the start of
  each game. Move notation is normalized for both White and Black.
- Stockfish searches for your side; the best move is drawn on a transparent,
  click-through overlay. You can move the piece yourself or explicitly enable
  automatic moves for the current session.
- The reader locks onto the matching piece theme, reuses capture resources,
  and can recover several missed half-moves after a temporary screen stall.

## Notes

- Calibration is saved across games. Keep the browser window in place; press
  F10 only if automatic relocation cannot recover after the board moves or
  resizes. Menus, animations, and result dialogs are retried automatically and
  do not require recalibration.
- If saved coordinates are stale at startup, ChessHelper scans the visible
  desktop for an 8x8 board and saves its location only after two legal reads.
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
- On macOS, the capture path explicitly reads the windows beneath the overlay,
  so the animated guide cannot contaminate board recognition.
