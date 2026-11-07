"""Fullscreen board calibration with a visible 8x8 preview grid."""
import tkinter as tk

import mss


def normalize_rect(points, min_size=160, aspect_tolerance=0.20):
    """Validate two opposite corner clicks and return left/top/right/bottom."""
    if len(points) != 2:
        return None
    (x1, y1), (x2, y2) = points
    left, right = sorted((int(x1), int(x2)))
    top, bottom = sorted((int(y1), int(y2)))
    width, height = right - left, bottom - top
    if width < min_size or height < min_size:
        return None
    if abs(width - height) / max(width, height) > aspect_tolerance:
        return None
    return [left, top, right, bottom]


def grid_lines(rect, divisions=8):
    """Return evenly spaced grid line coordinates including both board edges."""
    left, top, right, bottom = rect
    xs = [round(left + i * (right - left) / divisions)
          for i in range(divisions + 1)]
    ys = [round(top + i * (bottom - top) / divisions)
          for i in range(divisions + 1)]
    return xs, ys


def window_geometry(width, height, left, top):
    """Tk geometry supporting negative virtual-desktop coordinates."""
    return f"{width}x{height}{left:+d}{top:+d}"


def run(timeout_s=None, parent=None):
    """Show live dimmed desktop and return calibrated physical pixel bounds.

    Click top-left then bottom-right OUTER edges of the 8x8 board. Enter
    confirms the visible grid, clicking during preview lets you redo, and
    Escape/right-click cancels. Returns None on cancel/timeout.
    """
    with mss.mss() as sct:
        mon = sct.monitors[0]

    # Reuse the application's Tcl/Tk interpreter. Creating a second Tk root
    # while the overlay root is withdrawn can leave both windows invisible on
    # Windows after calibration closes.
    root = tk.Toplevel(parent) if parent is not None else tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.4)
    root.geometry(window_geometry(
        mon["width"], mon["height"], mon["left"], mon["top"]))
    canvas = tk.Canvas(root, bg="#000000", highlightthickness=0, bd=0,
                       cursor="crosshair")
    canvas.pack(fill="both", expand=True)

    pts = []
    screen_pts = []
    result = {"rect": None}
    state = {"preview": False}
    width = mon["width"]
    message = canvas.create_text(
        width // 2, 45, fill="#ffffff", font=("Segoe UI", 18, "bold"),
        width=max(300, width - 120), justify="center",
        text="Click the TOP-LEFT OUTER EDGE of the 8x8 board\n"
             "Esc or right-click cancels")
    save_button = None

    def clear_preview():
        canvas.delete("preview")
        canvas.delete("point")

    def cancel(_event=None):
        result["rect"] = None
        root.destroy()

    def restart(_event=None):
        if state["preview"]:
            state["preview"] = False
            result["rect"] = None
            pts.clear()
            screen_pts.clear()
            clear_preview()
            canvas.itemconfig(
                message,
                text="Click the TOP-LEFT OUTER EDGE of the 8x8 board\n"
                     "Esc or right-click cancels")

    def accept(_event=None):
        if state["preview"]:
            root.destroy()

    def draw_preview(rect):
        left, top, right, bottom = rect
        local_left, local_right = left - mon["left"], right - mon["left"]
        local_top, local_bottom = top - mon["top"], bottom - mon["top"]
        for i in range(9):
            x = round(local_left + i * (local_right - local_left) / 8)
            y = round(local_top + i * (local_bottom - local_top) / 8)
            canvas.create_line(x, local_top, x, local_bottom,
                               fill="#00ff80", width=2 if i in (0, 8) else 1,
                               tags="preview")
            canvas.create_line(local_left, y, local_right, y,
                               fill="#00ff80", width=2 if i in (0, 8) else 1,
                               tags="preview")

    def click(event):
        local_x, local_y = event.x, event.y
        if state["preview"]:
            if (width // 2 - 105 <= local_x <= width // 2 + 105
                    and 92 <= local_y <= 132):
                accept()
                return
            restart()
        screen_x = event.x_root
        screen_y = event.y_root
        pts.append((local_x, local_y))
        screen_pts.append((screen_x, screen_y))
        canvas.create_oval(local_x - 5, local_y - 5,
                           local_x + 5, local_y + 5,
                           fill="#ff0040", outline="#ffffff", width=2,
                           tags="point")
        if len(pts) == 1:
            canvas.itemconfig(
                message,
                text="Now click the BOTTOM-RIGHT OUTER EDGE of the 8x8 board")
            return

        # Use screen coordinates from Tk's window manager. This remains
        # correct even when the primary monitor has a nonzero desktop origin.
        rect = normalize_rect(screen_pts)
        if rect is None:
            pts.clear()
            screen_pts.clear()
            clear_preview()
            canvas.itemconfig(message,
                              text="Selection must be near-square and at least 160 px. "
                                   "Click the TOP-LEFT OUTER EDGE again.")
            return
        result["rect"] = rect
        state["preview"] = True
        draw_preview(rect)
        nonlocal save_button
        if save_button is None:
            save_button = canvas.create_rectangle(
                width // 2 - 105, 92, width // 2 + 105, 132,
                fill="#006b42", outline="#ffffff", width=2, tags="save")
            canvas.create_text(width // 2, 112, text="SAVE GRID",
                               fill="#ffffff", font=("Segoe UI", 16, "bold"),
                               tags="save")
        canvas.itemconfig(
            message,
            text="Check the green lines against every square. "
                 "Click SAVE GRID to finish; click elsewhere to redo; Esc cancels.")

    canvas.bind("<Button-1>", click)
    canvas.bind("<Button-3>", cancel)
    root.bind_all("<Escape>", cancel)
    root.bind_all("<Return>", accept)
    root.bind_all("<BackSpace>", restart)
    root.focus_force()
    if timeout_s:
        root.after(int(timeout_s * 1000), cancel)
    if parent is not None:
        root.grab_set()
        parent.wait_window(root)
    else:
        root.mainloop()
    return result["rect"]
