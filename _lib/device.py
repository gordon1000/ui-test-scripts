# -*- coding: utf-8 -*-
"""Windows + Chrome window discovery, focus, Airtest connect.

Compatible with AirtestIDE Python 3.6 and qb_env 3.13.
"""
from __future__ import print_function

import sys
import time

from . import config


def find_game_windows(title_substrings=None):
    """Return visible top-level windows whose title matches any substring."""
    import win32gui

    hints = title_substrings or config.WINDOW_TITLE_SUBSTRINGS
    hits = []

    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            return
        if any(h in title for h in hints):
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            hits.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                    "origin": (left, top),
                    "size": (right - left, bottom - top),
                    "rect": (left, top, right, bottom),
                }
            )

    win32gui.EnumWindows(enum_handler, None)
    return hits


def read_window_rect(hwnd):
    """Fresh outer rect after focus / user moves."""
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(int(hwnd))
    return {
        "hwnd": int(hwnd),
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
        "origin": (left, top),
        "size": (right - left, bottom - top),
        "rect": (left, top, right, bottom),
        "title": win32gui.GetWindowText(int(hwnd)) or "",
    }


def focus_window(hwnd):
    """
    Bring window forward WITHOUT ShowWindow(SW_RESTORE).

    SW_RESTORE un-maximizes / resizes and feels like a title-bar double-click.
    Also avoid synthetic clicks on the title bar.
    """
    import win32con
    import win32gui
    import win32process

    hwnd = int(hwnd)
    if win32gui.IsIconic(hwnd):
        # Only restore if minimized; do not toggle normal/maximized
        win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    try:
        # Allow SetForegroundWindow across threads when possible
        fg = win32gui.GetForegroundWindow()
        if fg:
            cur_tid, _ = win32process.GetWindowThreadProcessId(fg)
            tgt_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            if cur_tid != tgt_tid:
                win32process.AttachThreadInput(cur_tid, tgt_tid, True)
                try:
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    win32process.AttachThreadInput(cur_tid, tgt_tid, False)
            else:
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
        else:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        print("[device] WARN focus_window: %r" % (e,), file=sys.stderr)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e2:
            print("[device] WARN SetForegroundWindow: %r" % (e2,), file=sys.stderr)

    time.sleep(0.15)


def connect_windows_device(retry=2, hwnd=None, foreground=False):
    """
    Connect Airtest Windows backend.

    If hwnd is set and CONNECT_WINDOW_HANDLE, bind to that window so touch()
    uses window-relative coordinates (no multi-monitor absolute confusion).
    foreground=False avoids Airtest/pywinauto's own SetForeground (we already focused gently).
    """
    from airtest.core.api import connect_device

    last = None
    for attempt in range(1, retry + 1):
        try:
            if hwnd is not None and config.CONNECT_WINDOW_HANDLE:
                # URI: Windows:///<handle>?foreground=False
                uri = "Windows:///%s?foreground=%s" % (
                    int(hwnd),
                    "True" if foreground else "False",
                )
                dev = connect_device(uri)
                config.set_device_window_relative(True)
                print("[device] connected window handle=%s (window-relative touch)" % (hwnd,), file=sys.stderr)
                return dev
            dev = connect_device("Windows:///")
            config.set_device_window_relative(False)
            print("[device] connected full desktop Windows:///", file=sys.stderr)
            return dev
        except Exception as e:
            last = e
            print("[device] connect attempt %s failed: %r" % (attempt, e), file=sys.stderr)
            config.set_device_window_relative(False)
    raise RuntimeError("connect_device Windows failed: %r" % (last,))


def setup_windows_chrome(focus=True, require_window=True, size_tolerance=None):
    """
    Find game Chrome window, gently focus, re-read rect, connect Airtest.

    Returns (device, window_info dict).
    """
    hits = find_game_windows()
    if not hits:
        msg = (
            "No Chrome window matching %r. Open the game tab and re-run."
            % (config.WINDOW_TITLE_SUBSTRINGS,)
        )
        if require_window:
            raise RuntimeError(msg)
        print("[device] WARN %s" % msg, file=sys.stderr)
        dev = connect_windows_device(hwnd=None)
        return dev, None

    if len(hits) > 1:
        print(
            "[device] WARN %s matching windows; using first: %r"
            % (len(hits), hits[0]["title"]),
            file=sys.stderr,
        )

    hwnd = hits[0]["hwnd"]
    if focus:
        focus_window(hwnd)

    # Critical: rect AFTER focus (never trust pre-focus size if anything moved)
    win = read_window_rect(hwnd)
    config.set_live_window(
        origin=win["origin"],
        size=win["size"],
        hwnd=win["hwnd"],
        title=win["title"],
    )

    tol = size_tolerance if size_tolerance is not None else config.SIZE_TOLERANCE_PX
    rw, rh = config.REF_SIZE
    lw, lh = win["size"]
    if abs(lw - rw) > tol or abs(lh - rh) > tol:
        print(
            "[device] WARN live size %s differs from REF_SIZE %s (tol=%s). Coords may drift."
            % (win["size"], config.REF_SIZE, tol),
            file=sys.stderr,
        )
    if win["origin"] != config.ORIGIN:
        print(
            "[device] info live origin %s (pinned ORIGIN=%s); using live for transforms."
            % (win["origin"], config.ORIGIN),
            file=sys.stderr,
        )

    dev = connect_windows_device(hwnd=hwnd, foreground=False)
    print(
        "[device] OK title=%r origin=%s size=%s window_rel_touch=%s"
        % (win["title"], win["origin"], win["size"], config.is_device_window_relative()),
        file=sys.stderr,
    )
    return dev, win
