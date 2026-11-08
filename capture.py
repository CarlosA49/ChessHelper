"""Fast screen capture of the calibrated board rectangle."""
import sys
import threading

import cv2
import mss
import numpy as np

_EXCLUDED_WINDOW_ID = None
_CAPTURE_LOCAL = threading.local()


def set_excluded_window(window_id):
    """On macOS, capture windows below this overlay window."""
    global _EXCLUDED_WINDOW_ID
    _EXCLUDED_WINDOW_ID = int(window_id) if window_id is not None else None


def _screen_capture():
    """Reuse one mss session per worker thread for long-running capture."""
    session = getattr(_CAPTURE_LOCAL, "session", None)
    if session is None:
        session = mss.mss()
        _CAPTURE_LOCAL.session = session
    return session


def close_thread_capture():
    """Release the current thread's persistent desktop capture resources."""
    session = getattr(_CAPTURE_LOCAL, "session", None)
    if session is not None:
        session.close()
        del _CAPTURE_LOCAL.session


def _bytes_to_bgr(data, width, height, bytes_per_row):
    """Convert CoreGraphics padded BGRA bytes into a compact BGR array."""
    rows = np.frombuffer(data, dtype=np.uint8).reshape(height, bytes_per_row)
    bgra = rows[:, :width * 4].reshape(height, width, 4)
    return bgra[:, :, :3].copy()


def _grab_macos_below_overlay(rect):
    import Quartz

    left, top, right, bottom = rect
    bounds = Quartz.CGRectMake(left, top, right - left, bottom - top)
    options = (Quartz.kCGWindowImageBoundsIgnoreFraming
               | Quartz.kCGWindowImageShouldBeOpaque
               | Quartz.kCGWindowImageNominalResolution)
    image = Quartz.CGWindowListCreateImage(
        bounds,
        Quartz.kCGWindowListOptionOnScreenBelowWindow,
        _EXCLUDED_WINDOW_ID,
        options,
    )
    if image is None:
        raise RuntimeError("macOS could not capture the board below the overlay")
    width = Quartz.CGImageGetWidth(image)
    height = Quartz.CGImageGetHeight(image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(image)
    provider = Quartz.CGImageGetDataProvider(image)
    data = bytes(Quartz.CGDataProviderCopyData(provider))
    return _bytes_to_bgr(data, width, height, bytes_per_row)


def grab(rect):
    """rect: [left, top, right, bottom] in physical pixels. Returns a BGR ndarray."""
    if sys.platform == "darwin" and _EXCLUDED_WINDOW_ID is not None:
        return _grab_macos_below_overlay(rect)
    left, top, right, bottom = rect
    sct = _screen_capture()
    mon = {"left": left, "top": top,
           "width": right - left, "height": bottom - top}
    shot = sct.grab(mon)
    img = np.asarray(shot)
    return img[:, :, :3].copy()  # BGRA -> BGR


def grab_virtual_screen():
    """Capture the whole virtual desktop and return ``(image, rect)``."""
    mon = _screen_capture().monitors[0]
    rect = [mon["left"], mon["top"],
            mon["left"] + mon["width"], mon["top"] + mon["height"]]
    return grab(rect), rect


def rects_are_near(first, second, tolerance=0.01):
    """Return whether two board rectangles differ only by matching jitter."""
    if not first or not second or len(first) != 4 or len(second) != 4:
        return False
    side = max(
        first[2] - first[0], first[3] - first[1],
        second[2] - second[0], second[3] - second[1],
    )
    allowed = max(4, round(side * tolerance))
    return all(abs(a - b) <= allowed for a, b in zip(first, second))


def find_relocated_rect(reference, desktop, desktop_rect,
                        min_score=0.58):
    """Find a moved/resized board using its last valid captured image.

    Matching is performed on blurred, downscaled images so changing pieces do
    not outweigh the board's persistent square/color structure.
    """
    if reference is None or desktop is None:
        return None
    rh, rw = reference.shape[:2]
    dh, dw = desktop.shape[:2]
    if min(rh, rw) < 160 or min(dh, dw) < 160:
        return None

    downscale = min(1.0, 1200.0 / dw)
    screen = cv2.resize(
        desktop, None, fx=downscale, fy=downscale,
        interpolation=cv2.INTER_AREA)
    screen = cv2.GaussianBlur(
        cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), (9, 9), 0)
    best = None
    for scale in (0.75, 0.875, 1.0, 1.125, 1.25):
        width = round(rw * scale * downscale)
        height = round(rh * scale * downscale)
        if width < 80 or height < 80:
            continue
        if width > screen.shape[1] or height > screen.shape[0]:
            continue
        template = cv2.resize(
            reference, (width, height), interpolation=cv2.INTER_AREA)
        template = cv2.GaussianBlur(
            cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), (9, 9), 0)
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _minimum, score, _min_at, location = cv2.minMaxLoc(result)
        if best is None or score > best[0]:
            best = (score, location, width, height)
    if best is None or best[0] < min_score:
        return None
    _score, (x, y), width, height = best
    left = desktop_rect[0] + round(x / downscale)
    top = desktop_rect[1] + round(y / downscale)
    right = desktop_rect[0] + round((x + width) / downscale)
    bottom = desktop_rect[1] + round((y + height) / downscale)
    return [left, top, right, bottom]
