# -*- coding: utf-8 -*-
"""Calibration and runtime flags. See docs/calibration.md.
"""
from __future__ import print_function

import os

# --- Shared paths (usable from any .air script after: from _lib import config) ---
# AirtestScripts/  (parent of _lib/)
SCRIPTS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Page packages under screens/ (scripts + promoted images + parent _captures/).
# See docs/image-storage-standard.md
#   screens/Common/quest_tab.png          ← HUD tab
#   screens/Common/_captures/Adventure_000_x_y.png
#   screens/Adventure/fastBtl.png
#   screens/Quest/tower.png
SCREENS_ROOT = os.path.join(SCRIPTS_ROOT, "screens")
COMMON = os.path.join(SCREENS_ROOT, "Common")

# Known page folder names (create under screens/ as needed)
PAGE_HOME = "Home"
PAGE_QUEST = "Quest"
PAGE_ADVENTURE = "Adventure"
PAGE_EVENT = "Event"
PAGE_COMMON = "Common"

# Legacy flat folder (pre-standard); still resolved by common_tpl if present
_LEGACY_COMMON = os.path.join(SCRIPTS_ROOT, "Common")


def page_dir(page):
    """Absolute path to screens/<page>/."""
    return os.path.join(SCREENS_ROOT, page)


def page_tpl(page, *parts):
    """Absolute path under screens/<page>/, e.g. page_tpl('Quest', 'tower.png')."""
    return os.path.join(SCREENS_ROOT, page, *parts)


def common_tpl(*parts):
    """Path under screens/Common/, with fallback to legacy AirtestScripts/Common/."""
    primary = os.path.join(COMMON, *parts)
    if os.path.isfile(primary):
        return primary
    legacy = os.path.join(_LEGACY_COMMON, *parts)
    if os.path.isfile(legacy):
        return legacy
    return primary  # default target for new files


# --- Chrome / game window identity ---
GAME_PAGE_TITLE = "Break | Start"
WINDOW_TITLE_SUBSTRINGS = [
    "Limit Break",
    "G",
]

# --- Pinned geometry (PMR conversion baseline; prefer live GetWindowRect at runtime) ---
COORD_MODE = "window"  # "window" | "absolute"
ORIGIN = (3840, 157)
REF_SIZE = (618, 1153)
PMR_ORIGIN = (3840, 157)

MONITOR2_ORIGIN = (3840, 0)
MONITOR2_SIZE = (1920, 1200)
DISPLAY_SCALE_PERCENT = 100

# Warn if live size differs more than this many pixels from REF_SIZE
SIZE_TOLERANCE_PX = 40

# Connect Airtest Windows device to the game hwnd so touch() uses window-relative coords.
# Safer than full-desktop absolute for multi-monitor setups.
CONNECT_WINDOW_HANDLE = True

# --- Template capture (Phase 3a) — OFF by default; not used for IDE report screenshots ---
# Enable via (any one):
#   1) env AIRTEST_CAPTURE=1  (CLI)
#   2) empty/any file CAPTURE next to the .air script, or AirtestScripts/CAPTURE  (AirtestIDE-friendly)
#   3) config.CAPTURE_TEMPLATES = True in code before clicks
CAPTURE_TEMPLATES = False  # runtime override; also set True by env/file at check time
CAPTURE_SIZE = 32
CAPTURE_DIR_NAME = "_captures"
# Marker filenames that turn capture ON when present (no extension required)
CAPTURE_MARKER_NAMES = ("CAPTURE", "CAPTURE.on", ".airtest_capture")


def _env_truthy(name):
    val = os.environ.get(name, "")
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "yes", "on", "y")


def _marker_file_present(directory):
    if not directory:
        return False
    try:
        for name in CAPTURE_MARKER_NAMES:
            p = os.path.join(directory, name)
            if os.path.isfile(p):
                return True
    except Exception:
        pass
    return False


def capture_enabled():
    """True if Phase 3a harvest is on.

    Order: env → CAPTURE_TEMPLATES flag → CAPTURE marker next to script →
    CAPTURE under AirtestScripts root.
    """
    if _env_truthy("AIRTEST_CAPTURE") or _env_truthy("AIRTEST_CAPTURE_TEMPLATES"):
        return True
    if CAPTURE_TEMPLATES:
        return True
    # Per-script / page (set by bind_script_dir)
    if _marker_file_present(_capture_air_dir) or _marker_file_present(_capture_base_dir):
        return True
    # Repo AirtestScripts/CAPTURE — enables all scripts without env (IDE-friendly)
    if _capture_base_dir:
        try:
            # walk up from .air folder to folder that contains _lib
            here = _capture_base_dir
            for _ in range(6):
                parent = os.path.dirname(here)
                if not parent or parent == here:
                    break
                if os.path.isdir(os.path.join(parent, "_lib")):
                    if _marker_file_present(parent):
                        return True
                    break
                here = parent
        except Exception:
            pass
    return False


def capture_status_reason():
    """Short string for logs explaining why capture is on/off."""
    if _env_truthy("AIRTEST_CAPTURE") or _env_truthy("AIRTEST_CAPTURE_TEMPLATES"):
        return "on (env AIRTEST_CAPTURE)"
    if CAPTURE_TEMPLATES:
        return "on (config.CAPTURE_TEMPLATES=True)"
    if _marker_file_present(_capture_base_dir):
        return "on (CAPTURE file in script dir)"
    if _capture_base_dir:
        here = _capture_base_dir
        for _ in range(6):
            parent = os.path.dirname(here)
            if not parent or parent == here:
                break
            if os.path.isdir(os.path.join(parent, "_lib")):
                if _marker_file_present(parent):
                    return "on (CAPTURE file in AirtestScripts/)"
                break
            here = parent
    return "off (set AIRTEST_CAPTURE=1 or create CAPTURE file — see docs/phase3a-image-harvest.md)"

# If True, skip Airtest try_log_screen full-desktop dumps on each click (report less useful).
# IDE --log still creates a log folder; this only reduces per-action snapshots when supported.
SKIP_ACTION_LOG_SCREEN = os.environ.get("AIRTEST_SKIP_LOG_SCREEN", "").strip() in (
    "1",
    "true",
    "True",
    "yes",
    "YES",
)

# Runtime state filled by device.setup_windows_chrome()
_live_origin = None
_live_size = None
_live_hwnd = None
_live_title = None
_capture_step = 0
_capture_base_dir = None  # folder that contains _captures/ (often the page dir, e.g. screens/Common)
_capture_air_dir = None  # the .air project folder (for CAPTURE marker next to script)
_capture_air_name = "script"  # .air project name for harvest filenames
# When connected to a window handle, touch expects window-relative coords
_device_window_relative = False


def set_live_window(origin, size, hwnd=None, title=None):
    global _live_origin, _live_size, _live_hwnd, _live_title
    _live_origin = origin
    _live_size = size
    _live_hwnd = hwnd
    _live_title = title


def get_origin():
    """Live window origin if set, else pinned ORIGIN."""
    return _live_origin if _live_origin is not None else ORIGIN


def get_size():
    return _live_size if _live_size is not None else REF_SIZE


def set_device_window_relative(flag):
    global _device_window_relative
    _device_window_relative = bool(flag)


def is_device_window_relative():
    return bool(_device_window_relative)


def reset_capture_step(base_dir=None, air_name=None, air_dir=None):
    """Reset harvest counter; optional capture root and .air project name for filenames."""
    global _capture_step, _capture_base_dir, _capture_air_name, _capture_air_dir
    _capture_step = 0
    if base_dir is not None:
        _capture_base_dir = base_dir
    if air_dir is not None:
        _capture_air_dir = air_dir
    if air_name is not None:
        name = str(air_name).strip()
        if name.lower().endswith(".air"):
            name = name[: -len(".air")]
        # safe single path segment
        for ch in '\\/:*?"<>|\0':
            name = name.replace(ch, "_")
        _capture_air_name = name or "script"


def get_capture_air_name():
    return _capture_air_name or "script"


def next_capture_step():
    global _capture_step
    step = _capture_step
    _capture_step += 1
    return step
