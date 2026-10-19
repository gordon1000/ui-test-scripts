# -*- coding: utf-8 -*-
"""32x32 (or NxN) crop harvest at click points — Phase 3a only.

Not used for AirtestIDE --log report screenshots (those are full-desktop by default).

Enable with env AIRTEST_CAPTURE=1 or config.CAPTURE_TEMPLATES = True.
Crops land under <page>/_captures/<air>_<step>_<x>_<y>.png (gitignored).
Example: Common/_captures/Adventure_000_307_1076.png
"""
from __future__ import print_function

import os
import sys

from . import config


def _default_capture_dir():
    if config._capture_base_dir:
        base = config._capture_base_dir
    else:
        base = os.getcwd()
    d = os.path.join(base, config.CAPTURE_DIR_NAME)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


def _as_bgr_ndarray(raw):
    """Normalize device snapshot / file to HxWx3 BGR uint8 for cv2.imwrite."""
    import numpy as np

    if raw is None:
        return None
    img = raw if isinstance(raw, np.ndarray) else np.array(raw)
    if img is None or getattr(img, "size", 0) == 0:
        return None
    # grayscale -> BGR
    if len(img.shape) == 2:
        import cv2

        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        import cv2

        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _snapshot_array():
    """Prefer Airtest device bitmap; fall back to full snapshot file."""
    import cv2
    from airtest.core.api import G, snapshot

    img = None
    try:
        if G.DEVICE is not None:
            raw = G.DEVICE.snapshot()
            img = _as_bgr_ndarray(raw)
    except Exception as e:
        print("[capture] WARN device.snapshot: %r" % (e,), file=sys.stderr)

    if img is not None:
        return img

    dest = _default_capture_dir()
    tmp = os.path.join(dest, "_tmp_full.png")
    try:
        snapshot(filename=tmp)
        img = cv2.imread(tmp)
    except Exception as e:
        print("[capture] WARN snapshot(file): %r" % (e,), file=sys.stderr)
        img = None
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return img


def _click_pixel_in_image(img, x, y, space):
    """
    Map click (x,y) in script space to pixel (cx,cy) inside the snapshot.

    Window-scoped device: snapshot is the game window → use window-relative.
    Full-desktop device: snapshot is desktop → use screen absolute.
    """
    h, w = img.shape[:2]
    mode = (space or config.COORD_MODE or "window").lower()

    from .coords import to_screen, window_to_screen

    if mode == "absolute":
        sx, sy = int(x), int(y)
        if config.is_device_window_relative():
            ox, oy = config.get_origin()
            return int(sx - ox), int(sy - oy)
        return sx, sy

    if mode == "normalized":
        rw, rh = config.get_size()
        wx, wy = int(float(x) * rw), int(float(y) * rh)
    else:
        wx, wy = int(x), int(y)

    # Window-bound Airtest device: bitmap ≈ client/window size
    if config.is_device_window_relative():
        # Sanity: if bitmap is huge (full desktop), treat as screen
        if w >= 2000 or h >= 1600:
            return window_to_screen(wx, wy)
        return wx, wy

    # Desktop device: screen pixels
    return to_screen(wx, wy, space="window" if mode != "absolute" else "absolute")


def save_crop_at_click(x, y, step=None, size=None, space="window", out_dir=None):
    """
    Snapshot, crop size x size centered on click point, save PNG.
    Capture BEFORE the click. Only when CAPTURE_TEMPLATES / AIRTEST_CAPTURE=1.
    Returns path string or None on failure.
    """
    import cv2

    size = int(size if size is not None else config.CAPTURE_SIZE)
    if step is None:
        step = config.next_capture_step()

    img = _snapshot_array()
    if img is None:
        print("[capture] FAIL: no screenshot", file=sys.stderr)
        return None

    h, w = img.shape[:2]
    cx, cy = _click_pixel_in_image(img, x, y, space)
    half = size // 2

    # Ideal centered box; clamp to image
    ideal_x0 = cx - half
    ideal_y0 = cy - half
    x0 = max(0, ideal_x0)
    y0 = max(0, ideal_y0)
    x1 = min(w, x0 + size)
    y1 = min(h, y0 + size)
    if x1 - x0 < size:
        x0 = max(0, x1 - size)
    if y1 - y0 < size:
        y0 = max(0, y1 - size)
    x1 = min(w, x0 + size)
    y1 = min(h, y0 + size)

    shifted = (x0 != ideal_x0) or (y0 != ideal_y0)
    if shifted:
        print(
            "[capture] WARN crop clamped/shifted at click=(%s,%s) px=(%s,%s) box=(%s,%s)-(%s,%s) img=%sx%s"
            % (x, y, cx, cy, x0, y0, x1, y1, w, h),
            file=sys.stderr,
        )

    crop = img[y0:y1, x0:x1]
    if crop is None or getattr(crop, "size", 0) == 0:
        print("[capture] FAIL empty crop at px=(%s,%s)" % (cx, cy), file=sys.stderr)
        return None

    # Pad if near edge produced smaller crop (keep fixed size for Template consistency)
    ch, cw = crop.shape[:2]
    if ch != size or cw != size:
        import numpy as np

        canvas = np.zeros((size, size, 3), dtype=crop.dtype)
        canvas[0:ch, 0:cw] = crop
        crop = canvas

    dest_dir = out_dir or _default_capture_dir()
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    # <air project name>_<step>_<x>_<y>.png
    air = config.get_capture_air_name()
    fname = "%s_%03d_%s_%s.png" % (air, int(step), int(x), int(y))
    path = os.path.join(dest_dir, fname)
    ok = cv2.imwrite(path, crop)
    if not ok:
        print("[capture] FAIL imwrite %s" % path, file=sys.stderr)
        return None
    print(
        "[capture] saved %s (click=%s,%s px=%s,%s size=%s win_rel_dev=%s)"
        % (path, x, y, cx, cy, size, config.is_device_window_relative()),
        file=sys.stderr,
    )
    return path
