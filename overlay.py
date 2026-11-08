"""Polished transparent, click-through, animated move overlay."""
import contextlib
import ctypes
import math
import os
import sys
import tkinter as tk

import mss

KEY = "#010203"
CYAN = "#19e6ff"
CYAN_SOFT = "#78f3ff"
NAVY = "#07141f"
NAVY_LIGHT = "#102a3a"
WHITE = "#f4fbff"


def screen_to_local(x, y, origin):
    """Convert absolute screen pixels to virtual-desktop canvas pixels."""
    return x - origin[0], y - origin[1]


def screen_size():
    with mss.mss() as sct:
        mon = sct.monitors[0]
        return mon["width"], mon["height"], mon["left"], mon["top"]


def window_geometry(width, height, left, top):
    """Tk geometry supporting monitors with negative coordinates."""
    return f"{width}x{height}{left:+d}{top:+d}"


def move_label_position(target, board_rect, origin, dimensions):
    """Place the move pill outside the board when screen space permits."""
    tx, ty = screen_to_local(*target, origin)
    left, top = screen_to_local(board_rect[0], board_rect[1], origin)
    right, bottom = screen_to_local(board_rect[2], board_rect[3], origin)
    width, height = dimensions
    half_w, half_h, gap = 76, 19, 10

    clamp_x = min(max(tx, half_w + gap), width - half_w - gap)
    clamp_y = min(max(ty, half_h + gap), height - half_h - gap)
    if top >= 2 * half_h + gap:
        return clamp_x, top - half_h - 6
    if width - right >= 2 * half_w + gap:
        return right + half_w + 6, clamp_y
    if height - bottom >= 2 * half_h + gap:
        return clamp_x, bottom + half_h + 6
    if left >= 2 * half_w + gap:
        return left - half_w - 6, clamp_y

    # Fullscreen boards have no outside space. Use the edge farthest from the
    # destination, which keeps the destination square and arrowhead exposed.
    y = bottom - half_h - gap if ty < (top + bottom) / 2 else top + half_h + gap
    return clamp_x, y


def _blend(first, second, amount):
    amount = max(0.0, min(1.0, amount))
    a = tuple(int(first[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
    return "#" + "".join(f"{value:02x}" for value in rgb)


def control_rows(platform=None):
    """Return platform-native labels for the persistent shortcut guide."""
    platform = platform or sys.platform
    modifier = "Cmd+Option" if platform == "darwin" else "Ctrl+Alt"
    return (
        ("F7", "Hide / show this guide"),
        ("F9", "Pause / resume"),
        ("F10", "Recalibrate board"),
        (f"{modifier}+B", "Switch side manually"),
        (f"{modifier}+A", "Detect side automatically"),
        (f"{modifier}+M", "Toggle automatic moves"),
        ("F8", "Quit ChessHelper"),
    )


class Overlay:
    FRAME_MS = 40

    def __init__(self):
        width, height, left, top = screen_size()
        self.origin = (left, top)
        self.dimensions = (width, height)
        self.board_rect = None
        self._arrow = None
        self._phase = 0.0
        self._status_ring = None
        self._animation_job = None
        self._controls_visible = True
        self._controls_state = ("white", True, False)
        self._mac_window = None
        self.capture_window_id = None
        self._window_title = f"ChessHelper Overlay {os.getpid()}"
        transparent_bg = "systemTransparent" if sys.platform == "darwin" else KEY

        self.root = tk.Tk()
        self.root.title(self._window_title)
        self.root.overrideredirect(True)
        self.root.geometry(window_geometry(width, height, left, top))
        self.root.attributes("-topmost", True)
        if sys.platform == "win32":
            self.root.attributes("-transparentcolor", KEY)
        elif sys.platform == "darwin":
            with contextlib.suppress(tk.TclError):
                self.root.attributes("-transparent", True)
        self.root.configure(bg=transparent_bg)
        self.canvas = tk.Canvas(
            self.root, bg=transparent_bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._click_through()
        self.root.update()
        self.capture_excluded = self._exclude_from_capture()
        self._animation_job = self.root.after(self.FRAME_MS, self._animate)

    def _hwnd(self):
        if sys.platform != "win32":
            return None
        return ctypes.windll.user32.GetParent(self.root.winfo_id())

    def _click_through(self):
        if sys.platform != "win32":
            return
        hwnd = self._hwnd()
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex |= (WS_EX_LAYERED | WS_EX_TRANSPARENT |
               WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)

    def _exclude_from_capture(self):
        """Keep the UI visible to the player but invisible to board capture."""
        if sys.platform == "darwin":
            return self._configure_macos_window()
        if sys.platform != "win32":
            return False
        WDA_EXCLUDEFROMCAPTURE = 0x00000011
        try:
            for _attempt in range(3):
                self.root.update_idletasks()
                if ctypes.windll.user32.SetWindowDisplayAffinity(
                        self._hwnd(), WDA_EXCLUDEFROMCAPTURE):
                    return True
            error = ctypes.windll.kernel32.GetLastError()
            print("Windows could not exclude the overlay from capture:", error)
            return False
        except (AttributeError, OSError) as error:
            print("Windows overlay capture protection failed:", error)
            return False

    def _configure_macos_window(self):
        """Apply native transparency, click-through, and capture exclusion."""
        try:
            from AppKit import (
                NSApplication,
                NSColor,
                NSFloatingWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
            )

            app = NSApplication.sharedApplication()
            windows = list(app.windows())
            window = next(
                (candidate for candidate in windows
                 if str(candidate.title()) == self._window_title),
                None,
            )
            if window is None and windows:
                # Override-redirect Tk windows do not expose their title on
                # every Tk/macOS combination. The overlay is the largest
                # window owned by this short-lived Python application.
                window = max(
                    windows,
                    key=lambda candidate: (
                        candidate.frame().size.width
                        * candidate.frame().size.height),
                )
            if window is None:
                return False
            window.setIgnoresMouseEvents_(True)
            window.setOpaque_(False)
            window.setBackgroundColor_(NSColor.clearColor())
            window.setHasShadow_(False)
            window.setLevel_(NSFloatingWindowLevel)
            window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary)
            self._mac_window = window
            self.capture_window_id = int(window.windowNumber())
            return True
        except Exception as error:
            print("macOS overlay setup failed:", error)
            return False

    def _rounded_rect(self, x1, y1, x2, y2, radius=16, **options):
        points = [
            x1 + radius, y1, x2 - radius, y1,
            x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2,
            x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius,
            x1, y1 + radius, x1, y1,
        ]
        return self.canvas.create_polygon(
            points, smooth=True, splinesteps=24, **options)

    def clear(self):
        """Remove the move guide while preserving the current status pill."""
        self._arrow = None
        self.canvas.delete("guide")
        self.canvas.delete("move_label")
        self.root.update_idletasks()

    def set_board_rect(self, rect):
        self.board_rect = tuple(rect) if rect else None

    def show_status(self, text):
        """Render a compact glass-like status pill with an animated indicator."""
        self.canvas.delete("status")
        x1, y1 = 18, 16
        x2 = min(self.dimensions[0] - 18, 548)
        y2 = 66
        self._rounded_rect(
            x1 + 3, y1 + 4, x2 + 3, y2 + 4, 17,
            fill="#02080d", outline="", tags="status")
        self._rounded_rect(
            x1, y1, x2, y2, 17,
            fill=NAVY, outline="#1f6072", width=1, tags="status")
        self.canvas.create_line(
            x1 + 18, y2 - 2, x2 - 18, y2 - 2,
            fill="#12798d", width=2, tags="status")
        cx, cy = x1 + 27, (y1 + y2) / 2
        self.canvas.create_oval(
            cx - 10, cy - 10, cx + 10, cy + 10,
            fill="#082d38", outline="", tags="status")
        self._status_ring = self.canvas.create_arc(
            cx - 8, cy - 8, cx + 8, cy + 8,
            start=0, extent=255, style=tk.ARC,
            outline=CYAN, width=3, tags="status")
        self.canvas.create_text(
            x1 + 49, cy, text=text, anchor="w",
            font=("Segoe UI Variable Display", 13, "bold"),
            fill=WHITE, width=max(180, x2 - x1 - 64), tags="status")
        self.root.update_idletasks()

    def show_controls(self, color, automatic, auto_move=False):
        """Render the hotkey guide and current side-detection mode."""
        self._controls_state = (color, automatic, auto_move)
        self.canvas.delete("controls")
        if not self._controls_visible:
            return

        width = min(350, self.dimensions[0] - 36)
        x2 = self.dimensions[0] - 18
        x1 = x2 - width
        y1 = 16 if self.dimensions[0] >= 1000 else 82
        y2 = y1 + 244
        self._rounded_rect(
            x1 + 3, y1 + 4, x2 + 3, y2 + 4, 17,
            fill="#02080d", outline="", tags="controls")
        self._rounded_rect(
            x1, y1, x2, y2, 17,
            fill=NAVY, outline="#1f6072", width=1, tags="controls")

        self.canvas.create_text(
            x1 + 16, y1 + 19, text="CHESSHELPER CONTROLS", anchor="w",
            font=("Segoe UI Variable Display", 10, "bold"),
            fill=CYAN_SOFT, tags="controls")
        mode = "AUTO" if automatic else "MANUAL"
        side = color.upper()
        self.canvas.create_text(
            x2 - 16, y1 + 19, text=f"{side} · {mode}", anchor="e",
            font=("Segoe UI Variable Display", 10, "bold"),
            fill=WHITE, tags="controls")

        move_color = "#72ff9b" if auto_move else "#8ca2ad"
        move_state = "ON" if auto_move else "OFF"
        self.canvas.create_text(
            x1 + 16, y1 + 43, text=f"AUTOMATIC MOVES: {move_state}",
            anchor="w", font=("Segoe UI Variable Display", 9, "bold"),
            fill=move_color, tags="controls")

        for index, (key, action) in enumerate(control_rows()):
            y = y1 + 70 + index * 23
            self.canvas.create_text(
                x1 + 16, y, text=key, anchor="w",
                font=("Cascadia Mono", 9, "bold"), fill=CYAN,
                tags="controls")
            self.canvas.create_text(
                x1 + 118, y, text=action, anchor="w",
                font=("Segoe UI Variable Text", 9), fill=WHITE,
                tags="controls")
        self.root.update_idletasks()

    def toggle_controls(self):
        """Hide or restore the persistent hotkey guide."""
        self._controls_visible = not self._controls_visible
        self.show_controls(*self._controls_state)

    def show_arrow(self, x1, y1, x2, y2, label):
        """Show an animated direction guide without covering its destination."""
        local_start = screen_to_local(x1, y1, self.origin)
        local_end = screen_to_local(x2, y2, self.origin)
        self._arrow = (*local_start, *local_end)
        self.canvas.delete("move_label")
        self._draw_move_label((x2, y2), label)
        self._draw_arrow()
        self.root.update_idletasks()

    def _draw_move_label(self, target, label):
        if self.board_rect:
            x, y = move_label_position(
                target, self.board_rect, self.origin, self.dimensions)
        else:
            x, y = screen_to_local(*target, self.origin)
            y -= 48
        half_w, half_h = 76, 19
        self._rounded_rect(
            x - half_w + 2, y - half_h + 3,
            x + half_w + 2, y + half_h + 3, 13,
            fill="#02080d", outline="", tags="move_label")
        self._rounded_rect(
            x - half_w, y - half_h, x + half_w, y + half_h, 13,
            fill=NAVY_LIGHT, outline=CYAN, width=1, tags="move_label")
        self.canvas.create_oval(
            x - half_w + 12, y - 4, x - half_w + 20, y + 4,
            fill=CYAN, outline="", tags="move_label")
        self.canvas.create_text(
            x - half_w + 29, y, text=label, anchor="w",
            font=("Segoe UI Variable Display", 12, "bold"),
            fill=WHITE, width=112, tags="move_label")

    def _draw_arrow(self):
        self.canvas.delete("guide")
        if self._arrow is None:
            return
        x1, y1, x2, y2 = self._arrow
        pulse = (math.sin(self._phase * math.tau) + 1.0) / 2.0
        glow = _blend("#08748a", CYAN_SOFT, pulse)

        # A dark separation stroke keeps the arrow readable on every square.
        self.canvas.create_line(
            x1, y1, x2, y2, arrow=tk.LAST,
            arrowshape=(22, 29, 11), width=15,
            fill="#00171f", capstyle=tk.ROUND, tags="guide")
        self.canvas.create_line(
            x1, y1, x2, y2, arrow=tk.LAST,
            arrowshape=(19, 26, 10), width=8 + round(2 * pulse),
            fill=glow, capstyle=tk.ROUND, tags="guide")
        self.canvas.create_line(
            x1, y1, x2, y2, arrow=tk.LAST,
            arrowshape=(17, 23, 9), width=4,
            fill="#d7fbff", capstyle=tk.ROUND, tags="guide")

        # Direction particles flow from source to destination.
        for index in range(3):
            progress = (self._phase + index / 3.0) % 1.0
            progress = 0.12 + progress * 0.68
            px = x1 + (x2 - x1) * progress
            py = y1 + (y2 - y1) * progress
            radius = 3 + round(2 * (1.0 - progress))
            self.canvas.create_oval(
                px - radius, py - radius, px + radius, py + radius,
                fill=WHITE, outline=CYAN, width=1, tags="guide")

        # The destination remains visible through two thin pulsing rings.
        base = 18 + round(3 * pulse)
        self.canvas.create_oval(
            x2 - base, y2 - base, x2 + base, y2 + base,
            outline=CYAN, width=3, tags="guide")
        outer = base + 6
        self.canvas.create_oval(
            x2 - outer, y2 - outer, x2 + outer, y2 + outer,
            outline=_blend("#0a4a59", CYAN, pulse), width=1, tags="guide")

    def _animate(self):
        try:
            self._phase = (self._phase + self.FRAME_MS / 1300.0) % 1.0
            self._draw_arrow()
            if self._status_ring is not None:
                self.canvas.itemconfigure(
                    self._status_ring, start=-self._phase * 360)
            self._animation_job = self.root.after(self.FRAME_MS, self._animate)
        except tk.TclError:
            self._animation_job = None

    def destroy(self):
        try:
            if self._animation_job is not None:
                self.root.after_cancel(self._animation_job)
            self.root.destroy()
        except Exception:
            pass
