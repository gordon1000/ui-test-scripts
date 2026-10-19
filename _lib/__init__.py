# Shared helpers for Queen's Blade Airtest scripts (Windows + Chrome).
# Keep this package importable on AirtestIDE Python 3.6 and qb_env 3.13.
#
# Prefer::
#   from _lib import common
#   common.back(); common.ok(); common.close()
from . import config
from . import common

__all__ = ["config", "common"]
