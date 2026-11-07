"""Fast screen capture of the calibrated board rectangle."""
import mss
import numpy as np


def grab(rect):
    """rect: [left, top, right, bottom] in physical pixels. Returns a BGR ndarray."""
    left, top, right, bottom = rect
    with mss.mss() as sct:
        mon = {"left": left, "top": top,
               "width": right - left, "height": bottom - top}
        shot = sct.grab(mon)
        img = np.asarray(shot)
        return img[:, :, :3].copy()  # BGRA -> BGR
