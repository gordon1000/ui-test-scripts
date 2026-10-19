from airtest.core.api import *
from pathlib import Path
import sys
import time

SCRIPT_FILE = Path(__file__).resolve()
# --- BEGIN IMPORTS (_lib) ---
def _ensure_lib():
    here = SCRIPT_FILE.parent
    for p in [here, *here.parents]:
        if (p / "_lib" / "config.py").is_file():
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)
            return p
    raise RuntimeError("Cannot find AirtestScripts/_lib")

_ensure_lib()
from _lib.device import (
    connect_windows_device,
    find_windows,
    focus_window,
)
from _lib.actions import click_xy, right_click_xy, pause, bind_script_dir, NewTemplate
from _lib import config as _cfg
from _lib import common

# --- END IMPORTS ---

# IDE-friendly init: device panel + log path when run from AirtestIDE
auto_setup(__file__)
bind_script_dir(__file__)
# Outlook icon lives on the taskbar (outside Chrome), so connect the full desktop
connect_windows_device(hwnd=None)

# --- BEGIN EVENTS ---
outlook_windows = find_windows([" - Outlook"])

if outlook_windows:
    # Reuse the existing Outlook window instead of clicking the taskbar icon.
    focus_window(outlook_windows[0]["hwnd"])
else:
    # Start Outlook from the taskbar when no matching window exists.
    NewTemplate(r"..\Outlook.png", threshold=0.85, pause=0.2).touch()

    # The taskbar click can start Outlook asynchronously. Wait for its window
    # to become visible (for example, "Inbox - Outlook").
    deadline = time.time() + 10
    while time.time() < deadline:
        outlook_windows = find_windows([" - Outlook"])
        if outlook_windows:
            break
        pause(0.5)

    if not outlook_windows:
        raise RuntimeError("Outlook window was not found after clicking the taskbar icon")
    
    # The dialog may take a moment to render after Outlook is focused.
    reopen_dialog = NewTemplate(
        r"..\Outlook-Reopen-dialog.png",
        threshold=0.85,
        pause=0.2
    )
    if reopen_dialog.exists(wait=5):
        NewTemplate(
            r"..\Outlook-Reopen-dialog-yes.png",
            threshold=0.75,
            pause=0.2
        ).touch()
# --- END EVENTS ---
