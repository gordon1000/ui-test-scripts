# -*- coding: utf-8 -*-
"""High-level pause / click helpers (coords + optional capture + templates)."""
from __future__ import print_function

import sys
import time

from . import config
from .coords import to_screen, to_touch_pos


def pause(seconds):
    """Sleep; ignores non-positive values."""
    if seconds and seconds > 0:
        time.sleep(float(seconds))


def _win32_click(sx, sy, button="left"):
    """Screen-absolute click via Win32 (reliable on multi-monitor)."""
    import win32api
    import win32con

    sx, sy = int(sx), int(sy)
    win32api.SetCursorPos((sx, sy))
    time.sleep(0.02)
    if button == "right":
        down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
    else:
        down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
    win32api.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(up, 0, 0, 0, 0)


def click_xy(x, y, space=None, capture=None, use_win32=True):
    """
    Click at (x, y) in window-relative space by default.

    Uses Win32 screen-absolute click by default (avoids Airtest touch coord quirks
    and avoids title-bar / wrong-monitor hits). Falls back to airtest touch.

    Does NOT enable 32x32 harvest unless CAPTURE_TEMPLATES / AIRTEST_CAPTURE=1.
    Full-desktop images under --log are from Airtest's report logger, not this.
    """
    do_capture = config.capture_enabled() if capture is None else bool(capture)
    if do_capture:
        from .capture import save_crop_at_click

        try:
            saved = save_crop_at_click(x, y, space=space or config.COORD_MODE)
            if not saved:
                print(
                    "[actions] WARN capture enabled but save_crop_at_click returned None",
                    file=sys.stderr,
                )
        except Exception as e:
            print("[actions] WARN capture failed: %r" % (e,), file=sys.stderr)

    touch_x, touch_y = to_touch_pos(x, y, space=space)
    sx, sy = to_screen(x, y, space=space)

    print(
        "[actions] click_xy window/input=(%s,%s) touch_pos=(%s,%s) screen=(%s,%s) win_rel_dev=%s capture=%s"
        % (x, y, touch_x, touch_y, sx, sy, config.is_device_window_relative(), do_capture),
        file=sys.stderr,
    )

    # Prefer Win32 absolute screen click — unambiguous multi-monitor behavior
    if use_win32:
        try:
            _win32_click(sx, sy, button="left")
            return sx, sy
        except Exception as e:
            print("[actions] WARN win32 click failed: %r; try airtest touch" % (e,), file=sys.stderr)

    from airtest.core.api import touch

    touch((touch_x, touch_y))
    return sx, sy


def right_click_xy(x, y, space=None, capture=None, use_win32=True):
    do_capture = config.capture_enabled() if capture is None else bool(capture)
    if do_capture:
        from .capture import save_crop_at_click

        save_crop_at_click(x, y, space=space or config.COORD_MODE)

    touch_x, touch_y = to_touch_pos(x, y, space=space)
    sx, sy = to_screen(x, y, space=space)
    print(
        "[actions] right_click_xy input=(%s,%s) screen=(%s,%s)" % (x, y, sx, sy),
        file=sys.stderr,
    )

    if use_win32:
        try:
            _win32_click(sx, sy, button="right")
            return sx, sy
        except Exception as e:
            print("[actions] WARN win32 right_click failed: %r" % (e,), file=sys.stderr)

    try:
        from airtest.core.api import G

        dev = G.DEVICE
        if hasattr(dev, "touch"):
            dev.touch((touch_x, touch_y), right_click=True)
            return sx, sy
    except Exception as e:
        print("[actions] WARN airtest right_click failed: %r" % (e,), file=sys.stderr)

    _win32_click(sx, sy, button="left")
    return sx, sy


# Image clicks: NewTemplate(...).touch() — see _lib/templates.py
from .templates import NewTemplate, resolve_template_path  # noqa: E402


def bind_script_dir(script_file):
    """Bind capture root + .air project name for harvest filenames.

    For ``screens/Common/Adventure.air/Adventure.py``:
      - air name: ``Adventure`` (used in ``Adventure_000_x_y.png``)
      - capture dir: ``screens/Common/_captures/`` (page folder, not inside .air)
    """
    import os

    try:
        from pathlib import Path

        script = Path(script_file).resolve()
        air_dir = script.parent  # .../Adventure.air
        if air_dir.name.endswith(".air"):
            air_name = air_dir.stem  # Adventure.air → Adventure
            # page package (Common, Adventure, FastBtl, …) holds shared _captures
            page_dir = air_dir.parent
            config.reset_capture_step(
                str(page_dir), air_name=air_name, air_dir=str(air_dir)
            )
        else:
            config.reset_capture_step(
                str(air_dir), air_name=air_dir.name, air_dir=str(air_dir)
            )
    except Exception:
        d = os.path.dirname(os.path.abspath(script_file))
        base = os.path.basename(d)
        name = base[: -len(".air")] if base.endswith(".air") else base
        parent = os.path.dirname(d) if base.endswith(".air") else d
        config.reset_capture_step(parent, air_name=name, air_dir=d)
    # Always log capture mode once so IDE users see why crops are missing
    try:
        cap_dir = os.path.join(config._capture_base_dir or "", config.CAPTURE_DIR_NAME)
        print(
            "[actions] Phase3a capture: %s  air=%s  out=%s"
            % (
                config.capture_status_reason(),
                config.get_capture_air_name(),
                cap_dir,
            ),
            file=sys.stderr,
        )
    except Exception:
        pass


