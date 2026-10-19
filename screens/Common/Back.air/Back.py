__author__ = "GK"

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
from _lib.device import setup_windows_chrome
from _lib.actions import click_xy, right_click_xy, pause, bind_script_dir, NewTemplate
from _lib import common

# --- END IMPORTS ---

# IDE-friendly init: device panel + log path when run from AirtestIDE
auto_setup(__file__)
bind_script_dir(__file__)
# Focus game Chrome + live window rect (Windows desktop; no Poco)
setup_windows_chrome()

# --- BEGIN EVENTS ---
common.back(wait=2, required=False)
# --- END EVENTS ---
