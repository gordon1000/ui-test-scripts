# -*- coding: utf-8 -*-
"""Ensure AirtestScripts root is on sys.path so `import _lib` works."""
from __future__ import print_function

import os
import sys


def ensure_lib_path(script_file=None):
    """
    Insert AirtestScripts/ (parent of _lib) onto sys.path.
    Works for scripts nested like Home/Foo.air/Foo.py.
    """
    if script_file is not None:
        start = os.path.dirname(os.path.abspath(script_file))
    else:
        start = os.getcwd()

    cur = start
    for _ in range(12):
        marker = os.path.join(cur, "_lib", "config.py")
        if os.path.isfile(marker):
            if cur not in sys.path:
                sys.path.insert(0, cur)
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # Fallback: this file lives in AirtestScripts/_lib/
    airtest_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if airtest_scripts not in sys.path:
        sys.path.insert(0, airtest_scripts)
    return airtest_scripts
