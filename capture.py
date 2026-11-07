"""Fast screen capture of the calibrated board rectangle."""
import sys

import mss
import numpy as np

_EXCLUDED_WINDOW_ID = None


def set_excluded_window(window_id):
    """On macOS, capture windows below this overlay window."""
    global _EXCLUDED_WINDOW_ID
    _EXCLUDED_WINDOW_ID = int(window_id) if window_id is not None else None


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
    with mss.mss() as sct:
        mon = {"left": left, "top": top,
               "width": right - left, "height": bottom - top}
        shot = sct.grab(mon)
        img = np.asarray(shot)
        return img[:, :, :3].copy()  # BGRA -> BGR
