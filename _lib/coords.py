# -*- coding: utf-8 -*-
"""Coordinate transforms: PMR absolute, window-relative, screen absolute."""
from __future__ import print_function

from . import config


def pmr_to_window(x, y, pmr_origin=None):
    """Desktop-absolute PMR point -> outer-window-relative using conversion origin."""
    ox, oy = pmr_origin if pmr_origin is not None else config.PMR_ORIGIN
    return int(x - ox), int(y - oy)


def window_to_screen(x, y, origin=None):
    """Window-relative -> current desktop screen coords."""
    ox, oy = origin if origin is not None else config.get_origin()
    return int(ox + x), int(oy + y)


def screen_to_window(x, y, origin=None):
    ox, oy = origin if origin is not None else config.get_origin()
    return int(x - ox), int(y - oy)


def to_screen(x, y, space=None):
    """Resolve (x,y) in COORD_MODE / space to desktop screen pixels."""
    mode = (space or config.COORD_MODE or "window").lower()
    if mode == "absolute":
        return int(x), int(y)
    if mode == "window":
        return window_to_screen(x, y)
    if mode == "normalized":
        w, h = config.get_size()
        return window_to_screen(int(float(x) * w), int(float(y) * h))
    raise ValueError("Unknown coordinate space: %r" % (mode,))


def to_touch_pos(x, y, space=None):
    """
    Coords for device.touch / mouse click.

    - If Airtest device is bound to the game window: window-relative.
    - Else (full desktop device): screen absolute.
    """
    mode = (space or config.COORD_MODE or "window").lower()
    if mode == "absolute":
        sx, sy = int(x), int(y)
        if config.is_device_window_relative():
            return screen_to_window(sx, sy)
        return sx, sy
    # window or normalized -> window-relative first
    if mode == "normalized":
        w, h = config.get_size()
        wx, wy = int(float(x) * w), int(float(y) * h)
    else:
        wx, wy = int(x), int(y)
    if config.is_device_window_relative():
        return wx, wy
    return window_to_screen(wx, wy)


def from_pmr_absolute(x, y, mode=None):
    """Convert a raw PMR desktop point into the space used by generated scripts."""
    mode = (mode or config.COORD_MODE or "window").lower()
    if mode == "absolute":
        return int(x), int(y)
    if mode == "window":
        return pmr_to_window(x, y)
    if mode == "normalized":
        wx, wy = pmr_to_window(x, y)
        w, h = config.REF_SIZE
        return (float(wx) / w if w else 0.0), (float(wy) / h if h else 0.0)
    raise ValueError("Unknown coordinate mode: %r" % (mode,))
