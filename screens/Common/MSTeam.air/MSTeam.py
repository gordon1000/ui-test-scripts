from airtest.core.api import *
from pathlib import Path
import sys

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
from _lib.device import setup_windows_chrome, connect_windows_device
from _lib.actions import click_xy, right_click_xy, pause, bind_script_dir, NewTemplate
from _lib import config as _cfg
from _lib import common

# --- END IMPORTS ---

# IDE-friendly init: device panel + log path when run from AirtestIDE
auto_setup(__file__)
bind_script_dir(__file__)
# Teams icon lives on the taskbar (outside Chrome), so connect the full desktop
connect_windows_device(hwnd=None)

# --- BEGIN EVENTS ---
NewTemplate(r"..\MSTeam.png", threshold=0.85, pause=0.2).touch()
# --- END EVENTS ---
