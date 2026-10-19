# -*- coding: utf-8 -*-
"""NewTemplate — preferred image API for IDE preview + pause/wait + Common paths.

**Dismiss / overlay changes:** re-read ``docs/dismiss-popup-scenarios.md``
**§0 Frozen requirements** before editing ``find`` / post-click recover /
``dismiss_popups`` behavior. Never press OK while Next Stage is visible (R1).

AirtestIDE often extracts image paths with a regex matching ``Template(`` / string
args. The name **NewTemplate** still contains ``Template(`` so many IDE builds
will preview the **literal** path string in your source.

Preferred::

    from _lib.actions import NewTemplate
    # Required (fail script if missing):
    NewTemplate(r"..\\quest.png", pause=0.2, wait=10, isCommon=True, required=True).touch()

    # Optional UI (promo close): False if missing, script continues
    NewTemplate(r"..\\Quest_000_541_206.png", pause=0.5).touch()
    # same via .touch(required=True) override

- ``pause`` — fixed delay before find (like PMR sleep)
- ``wait`` — poll for image up to N seconds; click returned ``(x, y)`` (no re-find)
- ``isCommon`` — runtime load ``screens/Common/<basename>``
- ``required`` — if True, missing image raises; if False, ``touch()`` → False
- ``which`` — when several copies match, pick by **relative** 3×3 layout among
  those hits (not full-screen regions — works when buttons sit close together)::

      best | topleft | topmiddle | topright
           | middleleft | center | middleright
           | bottomleft | bottommiddle | bottomright

- ``dismiss_popups`` — default **False** (plain find + single click, no
  popup handling). If True:
  (1) target **missing** and **required=True** → after grace, walk
  next_stage → ok → close;
  (1b) target **missing** and **required=False** → after grace, **return
  miss without** auto-dismiss (do **not** press Close on the host dialog —
  Daily Select Free-already-sold case). Overlay clear still applies when
  the optional target *is* found under next_stage/ok;
  (2) target **found** but **next_stage/ok** also visible → clear that
  overlay first (dimmed HUD), then re-find;
  (3) co-visible **Close** alone is not an overlay (Collect+Close dialogs);
  (4) after a raw click: if next_stage/ok still up and the **target still
  matches**, dismiss and re-touch; if the target is **gone** but overlay
  remains, **drain** next_stage/ok then treat as success (one-shot /
  navigation — do not leave stacked result panels for the next step).
  After a dismiss, keep polling the target; re-probe the dismiss list on a
  short gap so stacked Next Stage panels can clear before the target appears.
- ``dismiss_retry_delay`` — seconds to wait after a successful popup dismiss
  before looking for the target again (default 0.8).
- ``confirm`` — after click, wait for this image (path / basename / list).
  If it never appears: dismiss next_stage/ok if any, **recheck confirm**
  (navigation may have succeeded under a late popup), then re-find/re-tap
  the target only if confirm is still missing; finally **raise**.
- ``confirm_change`` — optional fallback: major pixel change (usually off;
  prefer ``confirm=`` image). Same hard-fail as image confirm.
- ``touch()`` / ``click()`` — same; returns **True** if clicked, **False**
  if not found (unless required)
- ``re_touch`` — default **True**. After the first intentional click, if
  next_stage/ok is still up and the target still matches, dismiss and
  **click the target again** (occlusion recover). Set **False** for hub
  rows that stay on screen under a result OK (e.g. Arena #1) so one loop
  iteration cannot fire the same row 3–4 times. **Does not** disable
  pre-click / find-time popup dismiss (``dismiss_popups`` still applies).

Matching notes
--------------
Default Airtest CVSTRATEGY often does mstpl → tpl → **sift**. Small 32×32 harvest
crops have almost no keypoints; SIFT then raises ``cv2.error`` and kills the script
instead of returning “not found”. NewTemplate forces a **tpl-only** strategy by
default and catches OpenCV errors so a miss is always a clean False.

Multiple same buttons (close together)::

    # Among matches, the bottom-most / center-x of that cluster
    NewTemplate("OK.png", isCommon=True, which="bottommiddle").touch()

Lag / unregistered click (image confirm — preferred)::

    # Click Battle, then require IdleBattle (fails script if never seen)
    NewTemplate("Battle.png", isCommon=True).click(confirm="IdleBattle.png")
    NewTemplate(r"..\\Start.png").touch(
        confirm=r"..\\Result.png", confirm_pause=0.8, confirm_timeout=5
    )
"""
from __future__ import print_function

import os
import sys
import time

from . import config

# Safe default for game HUD / 32x32 harvest: pure template match only.
# Avoid sift/surf/kaze — they crash or false-match on tiny crops.
DEFAULT_CVSTRATEGY = ("tpl",)

# Post-click confirm (image preferred; pixel-diff is opt-in only)
_CONFIRM_PAUSE_S = 0.25  # fixed delay after click before looking for confirm
_CONFIRM_POLL_S = 0.25
_CONFIRM_TIMEOUT_S = 2.5
_CONFIRM_RETRIES = 2  # extra re-clicks after the first
# Confirm markers are often soft page art (banners), not crisp HUD buttons.
# Do not inherit a high click threshold (e.g. Back 0.84) for confirm match —
# Limited Summon entry matched ~0.77 with thr=0.75 while Back uses 0.84.
_CONFIRM_DEFAULT_THRESHOLD = 0.75
_DISMISS_RETRY_DELAY_S = 0.8  # after popup dismiss, before re-searching target
# When wait is None/short, still prefer the target over immediately clicking Close
# (screens like Forge have a real Close that must not be treated as a blocker).
_PRE_DISMISS_GRACE_S = 1.5  # poll target this long before first auto-dismiss
_POST_DISMISS_POLL_S = 4.0  # keep polling target at least this long after a dismiss
# After a successful dismiss, allow another try_dismiss soon while target is
# still missing (stacked Next Stage). Independent of _POST_DISMISS_POLL_S.
_POST_DISMISS_REPROBE_S = 0.4
_DISMISS_FIND_INTERVAL_S = 0.5  # poll interval while waiting for target
# After a raw click: re-touch only when next_stage/ok is still up *and* the
# target still matches (occluded). If the target art is gone (chest claimed),
# drain remaining overlays then success — do not raise on re-find miss.
_POST_CLICK_POPUP_RECOVERIES = 3
_POST_CLICK_SETTLE_S = 0.25
# Extra try_dismiss_popup rounds after one-shot (target gone, overlay up).
# Drain uses try_dismiss_popup(quick=True) so each round does not burn multi-
# second empty rescans — critical for Battle → SkipAll timing.
_POST_CLICK_DRAIN_ROUNDS = 2

# Optional pixel-diff fallback (confirm_change=True) — coarse only
_CHANGE_MAX_SIDE = 48
_CHANGE_BLOCK = 6
_CHANGE_BLOCK_MEAN_THR = 18.0
_CHANGE_MIN_BLOCK_FRAC = 0.22
_CHANGE_GLOBAL_MEAN_THR = 14.0

# which= values: best confidence, or relative 3x3 among match cluster.
# Target (rx, ry) in [0,1] over the bounding box of *hits only* (not full screen).
# left=0, middle=0.5, right=1; top=0, center=0.5, bottom=1
_WHICH_GRID = {
    "best": None,
    "topleft": (0.0, 0.0),
    "topmiddle": (0.5, 0.0),
    "topright": (1.0, 0.0),
    "middleleft": (0.0, 0.5),
    "center": (0.5, 0.5),
    "middleright": (1.0, 0.5),
    "bottomleft": (0.0, 1.0),
    "bottommiddle": (0.5, 1.0),
    "bottomright": (1.0, 1.0),
}
_WHICH_VALID = frozenset(_WHICH_GRID.keys())

# Exceptions that must abort the script (no soft "not found" / no re-click).
_HARD_ERROR_TYPE_NAMES = frozenset(
    {
        "FileNotExistError",
        "FileNotFoundError",
        "IsADirectoryError",
        "PermissionError",
        "TemplateInputError",
        "NoDeviceError",
        "DeviceConnectionError",
        "ScreenError",
        "AdbError",
        "AdbShellError",
        "LocalDeviceError",
        "MemoryError",
        "SystemError",
        "OSError",
        "IOError",
    }
)


def is_hard_system_error(exc):
    """True for missing files / IO / device failures — must not soft-retry.

    Public so ``common.try_dismiss_popup`` and similar can re-raise hard faults
    while treating ordinary match misses as soft failures.
    """
    if exc is None:
        return False
    if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
        return True
    if isinstance(exc, (OSError, IOError, MemoryError, SystemError)):
        return True
    # Airtest aircv (may not subclass OSError)
    try:
        from airtest.aircv.error import FileNotExistError, TemplateInputError

        if isinstance(exc, (FileNotExistError, TemplateInputError)):
            return True
    except ImportError:
        pass
    try:
        from airtest.core.error import (
            AdbError,
            AdbShellError,
            DeviceConnectionError,
            LocalDeviceError,
            NoDeviceError,
            ScreenError,
        )

        if isinstance(
            exc,
            (
                NoDeviceError,
                DeviceConnectionError,
                LocalDeviceError,
                ScreenError,
                AdbError,
                AdbShellError,
            ),
        ):
            return True
    except ImportError:
        pass
    # Name-based fallback when airtest stubs / older builds differ
    return type(exc).__name__ in _HARD_ERROR_TYPE_NAMES


# Back-compat private alias
_is_hard_system_error = is_hard_system_error


def _path_looks_relative(filename):
    """True if filename has dirs / .. and should not use screens/Common/."""
    s = str(filename).replace("/", "\\").strip()
    if not s:
        return False
    if s.startswith("..") or "\\..\\" in s or s.startswith(".\\"):
        return True
    # any directory component (not a bare basename)
    base = os.path.basename(s)
    return base != s and ("\\" in s or "/" in str(filename))


def resolve_template_path(filename, isCommon=False):
    """Resolve a template path for runtime (absolute when possible).

    Bare / relative names (when bound to a ``.air`` script) search in this
    order — **page packages before the ``.air`` folder** so confirm markers and
    promoted art under ``screens/<Page>/`` win over stale harvest copies that
    sometimes sit next to the script::

        1. parent of .air  (page / child screen folder)
        2. one folder above that (hub page, e.g. Home from LimitedSummon/)
        3. two folders above
        4. inside the .air project folder (last among local)
        5. screens/ root, then Common

    Prefer explicit ``r\"..\\\\img.png\"`` in scripts when possible.
    """
    fn = str(filename)
    # Relative/sibling paths are never Common assets
    if isCommon and not _path_looks_relative(fn):
        base = os.path.basename(fn.replace("\\", "/"))
        return config.common_tpl(base)

    if os.path.isabs(fn) and os.path.isfile(fn):
        return fn
    if os.path.isfile(fn):
        return os.path.abspath(fn)

    air = getattr(config, "_capture_air_dir", None)
    if air:
        page = os.path.dirname(air)  # parent of Foo.air
        base = os.path.basename(fn.replace("\\", "/"))
        parent = os.path.dirname(page)
        grand = os.path.dirname(parent) if parent else None
        # Page-first: avoid stale PNGs left inside .air/ shadowing hub art
        # (e.g. Home-LimitedSummon_000_*.png harvest 32x32 vs real Home 52x10).
        candidates = [
            os.path.normpath(os.path.join(page, fn)),
            os.path.normpath(os.path.join(page, base)),
            os.path.normpath(os.path.join(parent, fn)),
            os.path.normpath(os.path.join(parent, base)),
        ]
        if grand:
            candidates.append(os.path.normpath(os.path.join(grand, fn)))
            candidates.append(os.path.normpath(os.path.join(grand, base)))
        candidates.append(os.path.normpath(os.path.join(air, fn)))
        candidates.append(os.path.normpath(os.path.join(air, base)))
        for cand in candidates:
            if os.path.isfile(cand):
                return cand

    cand = os.path.normpath(os.path.join(config.SCREENS_ROOT, fn))
    if os.path.isfile(cand):
        return cand

    # Basename search one level under each page folder is too heavy; Common last
    base = os.path.basename(fn.replace("\\", "/"))
    cand = config.common_tpl(base)
    if os.path.isfile(cand):
        return cand

    # Prefer absolute-ish best guess for error messages (page-relative)
    if air:
        return os.path.normpath(os.path.join(os.path.dirname(air), fn))
    return fn


class NewTemplate(object):
    """Image match + click helper (IDE-friendly name, Common paths, pause/wait).

    Parameters
    ----------
    filename : str
        Image path **string literal** for AirtestIDE thumbnails
        (e.g. ``r"..\\quest.png"`` from ``screens/Common/Some.air/``).
    threshold : float
        Match threshold (same as Airtest Template).
    pause : float
        Fixed seconds to sleep **before** find when calling ``.touch()`` / ``.find()``.
    wait : float or None
        If set and > 0, poll for image up to this many seconds, then click match xy.
        If None/0, one-shot find (image must already be on screen).
    isCommon : bool
        If True, runtime file is ``screens/Common/<basename(filename)>``.
    cv_strategy : sequence of str or None
        Airtest matching methods for this template only (default: ``("tpl",)``).
        Pass ``None`` to use global ``ST.CVSTRATEGY`` (can include sift — not recommended
        for 32×32 crops).
    required : bool
        If True (default), missing image raises RuntimeError from ``.touch()`` / ``.find()``.
        If False, returns None/False so optional UI can be skipped.
        Alias: ``raise_on_miss`` (same meaning).
    which : str
        When several copies match, which one to use:

        - ``"best"`` — highest confidence (default)
        - relative 3×3 among the **match cluster** (closest hit to that
          corner/edge of the hits' bounding box — good for close buttons):
          ``topleft``, ``topmiddle``, ``topright``,
          ``middleleft``, ``center``, ``middleright``,
          ``bottomleft``, ``bottommiddle``, ``bottomright``
    dismiss_popups : bool
        Default False. If True, missing target may walk the common dismiss list
        (next_stage → ok → close; never OK while Next Stage visible — see
        docs/dismiss-popup-scenarios.md §0). Target is polled first (pre-dismiss
        grace); after a dismiss, target is polled again (post-dismiss window)
        even when ``wait`` is None. Set False on dismiss buttons to avoid recursion.
    dismiss_retry_delay : float
        Seconds to sleep after a successful dismiss click before searching
        for the original target again (default 0.8).
    confirm : str or sequence or None
        After click, require this image (or any of a list) to appear.
        Paths resolve like other templates; bare names use ``confirm_isCommon``.
        Example: ``confirm="IdleBattle.png"`` or ``confirm=r"..\\Result.png"``.
    confirm_isCommon : bool or None
        Where to load ``confirm`` basenames. None → True if this template
        isCommon, else False (still tries Common fallback via resolve).
    confirm_pause : float
        Fixed seconds to sleep **after** each click before looking for the
        confirm image (or running pixel-diff). Default 0.25.
    confirm_timeout : float
        Seconds to poll for the confirm image after ``confirm_pause``
        (each click attempt).
    confirm_retries : int
        Extra re-clicks if confirm image never appears (default 2).
    confirm_change : bool
        If True and ``confirm`` is not set, fall back to major pixel-diff
        (usually leave False; prefer an explicit confirm image).
        Missing confirm (image or pixel) always raises after retries.
    """

    def __init__(
        self,
        filename,
        threshold=0.85,
        pause=0.8,
        wait=None,
        isCommon=False,
        target_pos=5,
        record_pos=None,
        resolution=(),
        rgb=False,
        scale_max=800,
        scale_step=0.005,
        cv_strategy=DEFAULT_CVSTRATEGY,
        required=True,
        raise_on_miss=None,
        which="best",
        dismiss_popups=False,
        dismiss_retry_delay=None,
        confirm=None,
        confirm_isCommon=None,
        confirm_pause=None,
        confirm_timeout=None,
        confirm_retries=None,
        confirm_change=False,
        re_touch=True,
    ):
        from airtest.core.api import Template

        self.qb_filename = str(filename)
        self.qb_is_common = bool(isCommon)
        self.qb_pause = float(pause or 0)
        self.qb_wait = None if wait is None else float(wait)
        if self.qb_wait is not None and self.qb_wait <= 0:
            self.qb_wait = None
        self.qb_cv_strategy = None if cv_strategy is None else tuple(cv_strategy)
        # required preferred; raise_on_miss kept as alias
        if raise_on_miss is not None:
            required = bool(raise_on_miss)
        self.qb_required = bool(required)
        self.qb_threshold = float(threshold)
        self.qb_which = self._normalize_which(which)
        self.qb_target_pos = target_pos
        self.qb_dismiss_popups = bool(dismiss_popups)
        self.qb_dismiss_retry_delay = (
            float(_DISMISS_RETRY_DELAY_S)
            if dismiss_retry_delay is None
            else max(0.0, float(dismiss_retry_delay))
        )
        self.qb_confirm = confirm
        self.qb_confirm_is_common = confirm_isCommon
        self.qb_confirm_pause = (
            float(_CONFIRM_PAUSE_S)
            if confirm_pause is None
            else max(0.0, float(confirm_pause))
        )
        self.qb_confirm_timeout = (
            float(_CONFIRM_TIMEOUT_S)
            if confirm_timeout is None
            else float(confirm_timeout)
        )
        self.qb_confirm_retries = (
            int(_CONFIRM_RETRIES)
            if confirm_retries is None
            else max(0, int(confirm_retries))
        )
        self.qb_confirm_change = bool(confirm_change)
        # True: post-click may re-click target under next_stage/ok (default).
        # False: one intentional click only; find/pre-click dismiss still runs.
        self.qb_re_touch = bool(re_touch)

        resolved = resolve_template_path(self.qb_filename, isCommon=self.qb_is_common)
        self.qb_resolved = resolved
        if not os.path.isfile(resolved):
            # _name() needs qb_filename/qb_resolved already set above
            print(
                "[NewTemplate][%s] WARN file not found: %s (from %r isCommon=%s)"
                % (
                    os.path.basename(str(filename).replace("/", os.sep)),
                    resolved,
                    filename,
                    isCommon,
                ),
                file=sys.stderr,
            )

        self._tpl = Template(
            resolved,
            threshold=threshold,
            target_pos=target_pos,
            record_pos=record_pos,
            resolution=resolution,
            rgb=rgb,
            scale_max=scale_max,
            scale_step=scale_step,
        )
        self.filepath = getattr(self._tpl, "filepath", resolved)
        self.threshold = threshold

    def __repr__(self):
        return "NewTemplate(%r -> %r, pause=%s, wait=%s, isCommon=%s)" % (
            self.qb_filename,
            self.qb_resolved,
            self.qb_pause,
            self.qb_wait,
            self.qb_is_common,
        )

    def match(self, screen):
        # Airtest Template API is match_in / match_all_in (not .match)
        return self._tpl.match_in(screen)

    def __getattr__(self, name):
        return getattr(self._tpl, name)

    def as_template(self):
        """Underlying airtest Template (absolute path)."""
        return self._tpl

    def _name(self):
        """Stable human label for logs (never AirtestIDE [Image #N])."""
        # Prefer the path/string the script author wrote
        raw = str(self.qb_filename or "")
        base = os.path.basename(raw.replace("/", os.sep))
        if base and not base.startswith("[Image"):
            return base
        res = str(self.qb_resolved or "")
        base2 = os.path.basename(res.replace("/", os.sep))
        if base2 and not base2.startswith("[Image"):
            return base2
        return raw or res or "?"

    def _log(self, msg, *args):
        """Stderr log always tagged with this template's image name."""
        try:
            body = msg % args if args else msg
        except Exception:
            body = "%s %r" % (msg, args)
        print("[NewTemplate][%s] %s" % (self._name(), body), file=sys.stderr)

    def _do_pause(self):
        if self.qb_pause and self.qb_pause > 0:
            from .actions import pause as _pause

            _pause(self.qb_pause)

    @staticmethod
    def _match_xy(pos):
        """Normalize airtest wait/loop_find return value to (x, y)."""
        if pos is None or pos is False:
            return None
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            try:
                return int(pos[0]), int(pos[1])
            except (TypeError, ValueError):
                return None
        if isinstance(pos, dict):
            r = pos.get("result")
            if isinstance(r, (list, tuple)) and len(r) >= 2:
                return int(r[0]), int(r[1])
        r = getattr(pos, "result", None)
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            return int(r[0]), int(r[1])
        return None

    def _with_cv_strategy(self):
        """Context: temporarily set ST.CVSTRATEGY for this find only."""
        from airtest.core.settings import Settings as ST

        class _Ctx(object):
            def __init__(self, strategy):
                self.strategy = strategy
                self._old = None

            def __enter__(self):
                if self.strategy is None:
                    return ST
                self._old = list(ST.CVSTRATEGY)
                ST.CVSTRATEGY = list(self.strategy)
                return ST

            def __exit__(self, *exc):
                if self._old is not None:
                    ST.CVSTRATEGY = self._old
                return False

        return _Ctx(self.qb_cv_strategy)

    def _snapshot_screen(self):
        from airtest.core.helper import G
        from airtest.core.settings import Settings as ST

        try:
            screen = G.DEVICE.snapshot(filename=None, quality=ST.SNAPSHOT_QUALITY)
        except Exception as e:
            print("[NewTemplate] WARN snapshot failed: %r" % (e,), file=sys.stderr)
            return None
        if screen is None:
            print("[NewTemplate] WARN screen is None", file=sys.stderr)
        return screen

    @staticmethod
    def _frame_to_gray(screen):
        """Device snapshot → uint8 grayscale ndarray, or None."""
        if screen is None:
            return None
        try:
            import cv2
            import numpy as np
        except Exception as e:
            print("[NewTemplate] WARN cv2/numpy unavailable for change check: %r" % (e,), file=sys.stderr)
            return None
        arr = np.asarray(screen)
        if arr.size == 0:
            return None
        if arr.ndim == 2:
            gray = arr
        elif arr.ndim == 3:
            ch = arr.shape[2]
            if ch == 4:
                gray = cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
            elif ch == 3:
                gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
            else:
                gray = arr[:, :, 0]
        else:
            return None
        if gray.dtype != np.uint8:
            gray = np.clip(gray, 0, 255).astype(np.uint8)
        return gray

    @classmethod
    def _downsample_for_change(cls, gray):
        """Blur + shrink so tiny VFX / animation barely moves the metric."""
        import cv2
        import numpy as np

        h, w = gray.shape[:2]
        if h < 2 or w < 2:
            return gray
        # Light blur kills 1px shimmer before downscale
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        scale = float(_CHANGE_MAX_SIDE) / float(max(h, w))
        if scale < 1.0:
            nw = max(8, int(round(w * scale)))
            nh = max(8, int(round(h * scale)))
            small = cv2.resize(blur, (nw, nh), interpolation=cv2.INTER_AREA)
        else:
            small = blur
        return small

    @classmethod
    def screen_change_stats(cls, before, after):
        """Compare two snapshots; return dict of scores (or empty on failure).

        Metrics are designed for **major** layout/UI shifts, not continuous
        battle animations: heavy downsample + block voting.
        """
        import numpy as np

        import cv2

        g0 = cls._frame_to_gray(before)
        g1 = cls._frame_to_gray(after)
        if g0 is None or g1 is None:
            return {}
        if g0.shape != g1.shape:
            # Window/device same size normally; tolerate rare mismatch
            g1 = cv2.resize(g1, (g0.shape[1], g0.shape[0]), interpolation=cv2.INTER_AREA)

        s0 = cls._downsample_for_change(g0)
        s1 = cls._downsample_for_change(g1)
        if s0.shape != s1.shape:
            s1 = cv2.resize(s1, (s0.shape[1], s0.shape[0]), interpolation=cv2.INTER_AREA)

        diff = cv2.absdiff(s0, s1)
        global_mean = float(np.mean(diff))

        # Regular grid: local animations change few cells; page flips many
        rows, cols = _CHANGE_BLOCK, _CHANGE_BLOCK
        cell_h = max(1, s0.shape[0] // rows)
        cell_w = max(1, s0.shape[1] // cols)
        changed = 0
        total = 0
        for ry in range(rows):
            for cx in range(cols):
                y0 = ry * cell_h
                y1 = (ry + 1) * cell_h if ry < rows - 1 else s0.shape[0]
                x0 = cx * cell_w
                x1 = (cx + 1) * cell_w if cx < cols - 1 else s0.shape[1]
                cell = diff[y0:y1, x0:x1]
                if cell.size == 0:
                    continue
                total += 1
                if float(np.mean(cell)) >= _CHANGE_BLOCK_MEAN_THR:
                    changed += 1
        block_frac = float(changed) / float(total) if total else 0.0
        return {
            "global_mean": global_mean,
            "block_frac": block_frac,
            "blocks_changed": changed,
            "blocks_total": total,
            "size": (int(s0.shape[1]), int(s0.shape[0])),
        }

    @classmethod
    def is_major_screen_change(cls, before, after, stats=None):
        """True if frames differ enough to count as a real UI reaction."""
        if stats is None:
            stats = cls.screen_change_stats(before, after)
        if not stats:
            return False
        if stats.get("global_mean", 0.0) >= _CHANGE_GLOBAL_MEAN_THR:
            return True
        if stats.get("block_frac", 0.0) >= _CHANGE_MIN_BLOCK_FRAC:
            return True
        return False

    def _target_still_present(self, which=None):
        """True if this template still matches current screen (best-effort)."""
        return self._snapshot_match_once(which=which) is not None

    def _normalize_confirm_list(self, confirm):
        """confirm=None|str|NewTemplate|sequence → list of items (may be empty)."""
        if confirm is None or confirm is False:
            return []
        if confirm is True:
            # True alone means "use pixel-diff" — handled by confirm_change
            return []
        if isinstance(confirm, (list, tuple)):
            return [c for c in confirm if c is not None and c is not False]
        return [confirm]

    def _confirm_is_common_for(self, path_or_name, confirm_isCommon=None):
        """Decide isCommon for a confirm image path/name.

        Relative paths (``..\\x.png``, ``subdir\\x.png``) are **never** Common,
        even if confirm_isCommon=True was passed by a helper default.
        """
        if isinstance(path_or_name, NewTemplate):
            return bool(path_or_name.qb_is_common)
        if _path_looks_relative(path_or_name):
            return False
        # Explicit override for bare basenames only
        if confirm_isCommon is not None:
            return bool(confirm_isCommon)
        if self.qb_confirm_is_common is not None:
            return bool(self.qb_confirm_is_common)
        # Bare basename → inherit this template's isCommon (usually Common HUD)
        return bool(self.qb_is_common)

    def _confirm_match_threshold(self, threshold=None):
        """Threshold for confirm image match (not the click-target threshold).

        Soft page markers often score below crisp HUD buttons. Cap at
        ``_CONFIRM_DEFAULT_THRESHOLD`` unless an explicit lower/higher value
        is passed.
        """
        if threshold is not None:
            return float(threshold)
        click_thr = float(self.qb_threshold)
        return min(click_thr, float(_CONFIRM_DEFAULT_THRESHOLD))

    def _make_confirm_template(self, path_or_name, confirm_isCommon=None, threshold=None):
        """Build a NewTemplate used only for post-click existence checks."""
        if isinstance(path_or_name, NewTemplate):
            return path_or_name
        thr = self._confirm_match_threshold(threshold)
        is_c = self._confirm_is_common_for(path_or_name, confirm_isCommon)
        tpl = NewTemplate(
            path_or_name,
            threshold=thr,
            pause=0,
            wait=None,
            isCommon=is_c,
            required=False,
            dismiss_popups=False,
            confirm=None,
            confirm_change=False,
            which="best",
        )
        if not os.path.isfile(tpl.qb_resolved):
            self._log(
                "WARN confirm image missing on disk: %s (from %r isCommon=%s) "
                "→ confirm will never succeed",
                tpl.qb_resolved,
                path_or_name,
                is_c,
            )
        else:
            self._log(
                "confirm image resolved: %s (from %r isCommon=%s)",
                tpl.qb_resolved,
                path_or_name,
                is_c,
            )
        return tpl

    def _any_confirm_present(self, confirm_tpls):
        """Return name of first confirm image currently on screen, else None.

        Uses quiet matching (no find/not-found spam).
        """
        for tpl in confirm_tpls or ():
            try:
                # Direct match — never go through find() logging
                if tpl._snapshot_match_once(which="best") is not None:
                    return tpl._name()
            except Exception as e:
                self._log("WARN confirm check failed for %s: %r", tpl._name(), e)
        return None

    def _do_confirm_pause(self, confirm_pause=None):
        """Fixed delay after click before confirm search (confirm_pause)."""
        p = self.qb_confirm_pause if confirm_pause is None else float(confirm_pause)
        if p and p > 0:
            from .actions import pause as _pause

            _pause(p)

    def _wait_for_confirm_images(
        self,
        confirm_tpls,
        timeout=None,
        pre_present=False,
        which=None,
        confirm_pause=None,
    ):
        """Poll until a confirm image is a valid success signal.

        Returns (ok, detail).

        Success always requires the **confirm image to be on screen** after the
        click (destination marker). It is never enough that the click target
        alone left — that false-confirmed full-screen swaps away from the
        intended page (e.g. Back leaving Sanctum while ``confirm=`` was the
        Explore/Sanctum entry that briefly matched under a prior UI).

        If the confirm image was **already** present before the click
        (``pre_present``), also require the *clicked* template to leave so a
        no-op click is not accepted. During page transitions the marker may
        disappear briefly; keep polling until it is back (and target gone when
        pre_present) or ``timeout`` elapses.

        ``confirm_pause`` runs once at the start (after the click), then
        ``timeout`` is the poll window.
        """
        if not confirm_tpls:
            return True, None
        self._do_confirm_pause(confirm_pause)
        t_out = self.qb_confirm_timeout if timeout is None else float(timeout)
        deadline = time.time() + max(0.05, t_out)
        conf_names = [t._name() for t in confirm_tpls]
        while True:
            name = self._any_confirm_present(confirm_tpls)
            if name is not None:
                if not pre_present:
                    return True, name
                # Confirm was already up pre-click — need click target to leave
                if not self._target_still_present(which=which):
                    return True, "%s+target_gone" % (name,)
            # If confirm is absent: keep waiting (transition / wrong page).
            # Do NOT treat "click target gone" alone as success — that used to
            # return True, "target_gone" and skip the destination check.
            if time.time() >= deadline:
                still_target = self._target_still_present(which=which)
                self._log(
                    "confirm poll timeout looking_for=%s pre_present=%s "
                    "confirm_now=%s target_still=%s",
                    conf_names,
                    pre_present,
                    name,
                    still_target,
                )
                return False, conf_names
            time.sleep(_CONFIRM_POLL_S)

    def _wait_for_click_effect(
        self, before, which=None, timeout=None, confirm_pause=None
    ):
        """Pixel-diff fallback: major change or target gone. (ok, reason, stats)."""
        if before is None:
            self._log("WARN no pre-click snapshot; skip change confirm")
            return True, "no_before", {}

        self._do_confirm_pause(confirm_pause)
        t_out = self.qb_confirm_timeout if timeout is None else float(timeout)
        deadline = time.time() + max(0.05, t_out)
        last_stats = {}
        while True:
            after = self._snapshot_screen()
            last_stats = self.screen_change_stats(before, after)
            if self.is_major_screen_change(before, after, stats=last_stats):
                return True, "major_change", last_stats
            if not self._target_still_present(which=which):
                return True, "target_gone", last_stats
            if time.time() >= deadline:
                return False, "timeout", last_stats
            time.sleep(_CONFIRM_POLL_S)

    def _match_all_raw(self, screen):
        """All template hits on screen (list of result dicts) or empty list."""
        try:
            with self._with_cv_strategy():
                # Template.match_all_in uses tpl find_all (not sift)
                results = self._tpl.match_all_in(screen)
        except Exception as e:
            self._log(
                "WARN match_all failed (treated as not found): %s: %s",
                type(e).__name__,
                e,
            )
            return []
        if not results:
            return []
        if isinstance(results, dict):
            return [results]
        try:
            return list(results)
        except TypeError:
            return []

    def _result_to_xy_conf(self, one):
        """One match_all entry → (x, y, confidence) or None."""
        if one is None:
            return None
        conf = 0.0
        if isinstance(one, dict):
            conf = float(one.get("confidence") or 0.0)
            r = one.get("result")
            if isinstance(r, (list, tuple)) and len(r) >= 2:
                return int(r[0]), int(r[1]), conf
        xy = self._match_xy(one)
        if xy is None:
            return None
        return xy[0], xy[1], conf

    @staticmethod
    def _normalize_which(which):
        """Return canonical which string; unknown → best with warning."""
        if which is None:
            return "best"
        key = str(which).strip().lower().replace("-", "").replace("_", "").replace(" ", "")
        # allow top_left / top-left style → topleft
        if key in _WHICH_VALID:
            return key
        print(
            "[NewTemplate] WARN unknown which=%r; valid=%s — using best"
            % (which, sorted(_WHICH_VALID)),
            file=sys.stderr,
        )
        return "best"

    def _select_which(self, hits, which=None, screen_size=None):
        """Pick one (x,y) from multi-match hits using relative layout of the cluster.

        hits: list of (x, y, conf). Grid names use the bounding box of *hits only*
        so close buttons still rank as top/middle/bottom relative to each other.
        ``screen_size`` is ignored (kept for call-site compatibility).
        """
        if not hits:
            return None
        w = self._normalize_which(self.qb_which if which is None else which)

        if len(hits) == 1 or w == "best" or _WHICH_GRID.get(w) is None:
            best = max(hits, key=lambda t: t[2])
            return (best[0], best[1])

        rx, ry = _WHICH_GRID[w]
        xs = [float(t[0]) for t in hits]
        ys = [float(t[1]) for t in hits]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max_x - min_x
        span_y = max_y - min_y

        def rel(t):
            x, y, conf = float(t[0]), float(t[1]), float(t[2])
            # Degenerate axis (all same x or y): every hit sits at 0.5 on that axis
            nx = 0.5 if span_x < 1e-6 else (x - min_x) / span_x
            ny = 0.5 if span_y < 1e-6 else (y - min_y) / span_y
            # Distance in relative space; tie-break higher confidence
            dist2 = (nx - rx) ** 2 + (ny - ry) ** 2
            return (dist2, -conf)

        best = min(hits, key=rel)
        return (best[0], best[1])

    def _snapshot_match_once(self, which=None):
        """One-shot match on current screen. Returns (x,y) or None. Never raises CV errors."""
        screen = self._snapshot_screen()
        if screen is None:
            return None

        w = self._normalize_which(self.qb_which if which is None else which)

        # Fast path: single best only
        if w == "best":
            try:
                with self._with_cv_strategy():
                    pos = self._tpl.match_in(screen)
            except Exception as e:
                self._log(
                    "WARN match failed (treated as not found): %s: %s",
                    type(e).__name__,
                    e,
                )
                return None
            return self._match_xy(pos)

        raw = self._match_all_raw(screen)
        hits = []
        for one in raw:
            t = self._result_to_xy_conf(one)
            if t is not None:
                hits.append(t)
        if not hits:
            return None

        xy = self._select_which(hits, which=w)
        if xy is not None:
            self._log(
                "multi-match n=%s which=%r picked=(%s,%s) "
                "cluster_bbox=(%s,%s)-(%s,%s) all=%s",
                len(hits),
                w,
                xy[0],
                xy[1],
                min(h[0] for h in hits),
                min(h[1] for h in hits),
                max(h[0] for h in hits),
                max(h[1] for h in hits),
                [(h[0], h[1], round(h[2], 3)) for h in hits],
            )
        return xy

    def find_all(self, wait=None, do_pause=True):
        """Return list of ``(x, y, confidence)`` for every match above threshold.

        Empty list if none. Never raises for miss.
        """
        if do_pause:
            self._do_pause()

        w = self.qb_wait if wait is None else float(wait)
        if w is not None and w <= 0:
            w = None

        if not os.path.isfile(self.qb_resolved):
            return []

        def _once():
            screen = self._snapshot_screen()
            if screen is None:
                return []
            hits = []
            for one in self._match_all_raw(screen):
                t = self._result_to_xy_conf(one)
                if t is not None:
                    hits.append(t)
            return hits

        if w is None:
            return _once()

        deadline = time.time() + float(w)
        interval = 0.5
        while True:
            hits = _once()
            if hits:
                return hits
            if time.time() >= deadline:
                return []
            time.sleep(interval)

    def _resolve_required(self, required=None, raise_on_miss=None):
        """Merge call-site required/raise_on_miss with constructor default."""
        if raise_on_miss is not None:
            return bool(raise_on_miss)
        if required is not None:
            return bool(required)
        return bool(self.qb_required)

    def find(
        self,
        wait=None,
        do_pause=True,
        required=None,
        raise_on_miss=None,
        which=None,
        dismiss_popups=None,
        dismiss_retry_delay=None,
    ):
        """Locate template; return ``(x, y)`` or ``None``.

        If ``dismiss_popups`` (default False; True enables the following)::

            A. **Target found + next_stage/ok** — dimmed under a victory/result
               overlay; clear overlay then re-find. Co-visible **Close** alone
               is ignored (Collect+Close chrome).
            B. **Missing target + required=True** — poll for
               ``_PRE_DISMISS_GRACE_S``, then full dismiss list
               (next_stage → ok → close).
            B2. **Missing target + required=False** — poll grace only, then
               return ``None`` **without** miss-driven dismiss (never auto-
               Close a host panel while probing optional Free/claim art).
            C. On successful dismiss: sleep ``dismiss_retry_delay``, then poll
               target for at least ``_POST_DISMISS_POLL_S``; re-run dismiss
               after ``_POST_DISMISS_REPROBE_S`` if the target is still missing
               (stacked Next Stage).
            D. After click: re-touch only if overlay remains **and** target
               still matches; target gone after click → drain overlays, success.

        Parameters
        ----------
        wait : float or None
            Override instance wait. ``None`` → one-shot for the target alone;
            with ``dismiss_popups`` still uses pre-dismiss grace + post-dismiss
            poll. ``>0`` → poll up to N seconds (deadline can extend after dismiss).
        do_pause : bool
            Apply configured ``pause`` before searching (default True).
        required : bool or None
            If True, raise RuntimeError when not found. None → constructor default.
        raise_on_miss : bool or None
            Alias for ``required`` (legacy).
        which : str or None
            Override constructor ``which`` (e.g. ``"bottommiddle"``).
        dismiss_popups : bool or None
            Override constructor; False for dismiss-button templates.
        dismiss_retry_delay : float or None
            Seconds after a successful dismiss before re-searching the target
            (default constructor value / 0.8).
        """
        if do_pause:
            self._do_pause()

        w = self.qb_wait if wait is None else float(wait)
        if w is not None and w <= 0:
            w = None

        must = self._resolve_required(required=required, raise_on_miss=raise_on_miss)
        which_eff = self._normalize_which(self.qb_which if which is None else which)
        do_dismiss = (
            self.qb_dismiss_popups
            if dismiss_popups is None
            else bool(dismiss_popups)
        )
        d_delay = (
            self.qb_dismiss_retry_delay
            if dismiss_retry_delay is None
            else max(0.0, float(dismiss_retry_delay))
        )
        # Optional quiet mode (used by exists / internal probes)
        quiet = bool(getattr(self, "_qb_quiet_find", False))

        if not os.path.isfile(self.qb_resolved):
            msg = "image file missing on disk: %s" % (self.qb_resolved,)
            if not quiet:
                self._log("%s", msg)
            if must:
                raise RuntimeError("NewTemplate[%s] %s" % (self._name(), msg))
            return None

        search_start = time.time()
        deadline = None if w is None else (search_start + float(w))
        interval = float(_DISMISS_FIND_INTERVAL_S)
        pre_grace = float(_PRE_DISMISS_GRACE_S) if do_dismiss else 0.0
        post_poll = float(_POST_DISMISS_POLL_S)
        re_dismiss_gap = float(_POST_DISMISS_REPROBE_S)
        # Earliest time we may auto-dismiss. After a successful clear, this is
        # only a short gap so stacked Next Stage can be drained while we still
        # poll the target for post_poll seconds (deadlines are independent).
        dismiss_after = search_start + pre_grace
        xy = None
        dismiss_rounds = 0
        max_dismiss_rounds = 12
        any_dismiss_cleared = False

        if not quiet:
            self._log(
                "find start thr=%.2f wait=%s which=%s dismiss=%s "
                "pre_grace=%.2fs file=%s",
                self.qb_threshold,
                w,
                which_eff,
                do_dismiss,
                pre_grace,
                self.qb_resolved,
            )

        def _after_dismiss_extend():
            """Sleep retry delay; extend target poll; allow soon re-dismiss.

            Returns (new_deadline, new_dismiss_after). Target may need several
            seconds to appear after a result panel; the next stacked Next Stage
            must not wait that full window before try_dismiss_popup runs again.
            """
            if d_delay > 0:
                time.sleep(d_delay)
            now2 = time.time()
            post_deadline = now2 + max(post_poll, d_delay)
            next_dismiss = now2 + max(0.0, re_dismiss_gap)
            if deadline is None:
                return post_deadline, next_dismiss
            return max(deadline, post_deadline), next_dismiss

        while True:
            xy = self._snapshot_match_once(which=which_eff)
            now = time.time()

            # Target matched — next_stage/ok may be a dimmer overlay (template
            # still sees the control). Close alone is dialog chrome, not overlay.
            if xy is not None and do_dismiss and dismiss_rounds < max_dismiss_rounds:
                try:
                    from .common import (
                        is_dismiss_image_name,
                        overlay_blocking,
                        try_dismiss_popup,
                    )

                    if not is_dismiss_image_name(self._name()):
                        blocker = overlay_blocking(for_target=self._name())
                        if blocker is not None:
                            if not quiet:
                                self._log(
                                    "target visible but overlay %s also present "
                                    "(dimmed / not pressable); "
                                    "try_dismiss_popup (round %s) file=%s",
                                    blocker,
                                    dismiss_rounds + 1,
                                    self.qb_resolved,
                                )
                            dismissed = bool(
                                try_dismiss_popup(for_target=self._name())
                            )
                            dismiss_rounds += 1
                            if dismissed:
                                any_dismiss_cleared = True
                                if not quiet:
                                    self._log(
                                        "cleared overlay; retry target after "
                                        "%.2fs (re-dismiss in %.2fs) file=%s",
                                        d_delay,
                                        re_dismiss_gap,
                                        self.qb_resolved,
                                    )
                                deadline, dismiss_after = _after_dismiss_extend()
                                xy = None
                                continue
                            if not quiet:
                                self._log(
                                    "overlay %s still blocking (not cleared); "
                                    "will not click dimmed target file=%s",
                                    blocker,
                                    self.qb_resolved,
                                )
                            dismiss_after = now + max(interval, 0.3)
                            xy = None
                            if deadline is None:
                                deadline = now + max(post_poll, 1.0)
                            time.sleep(interval)
                            continue
                except Exception as e:
                    self._log("WARN overlay-target dismiss failed: %r", e)

            if xy is not None:
                break

            # Target still missing — maybe auto-dismiss, but only after grace /
            # after a short post-clear re-dismiss gap (not the full target poll).
            # Optional (required=False): never miss-drive Close/ok/next_stage —
            # host dialogs (Daily Select) always have Close chrome; pressing it
            # exits the panel and skips later steps (Exclaim). Soft miss keeps
            # polling until wait/grace ends, but does not call try_dismiss_popup.
            if (
                do_dismiss
                and not must
                and now >= dismiss_after
                and dismiss_rounds == 0
            ):
                if not quiet:
                    self._log(
                        "target missing; required=False — skip miss-driven "
                        "dismiss (no auto-Close) file=%s",
                        self.qb_resolved,
                    )
                # One log only; keep polling if wait deadline still open.
                dismiss_rounds = 1
                dismiss_after = now + 1e9  # do not re-enter this branch
            if (
                do_dismiss
                and must
                and now >= dismiss_after
                and dismiss_rounds < max_dismiss_rounds
            ):
                dismissed = False
                try:
                    from .common import try_dismiss_popup

                    if not quiet:
                        self._log(
                            "target missing; try_dismiss_popup (round %s) file=%s",
                            dismiss_rounds + 1,
                            self.qb_resolved,
                        )
                    dismissed = bool(
                        try_dismiss_popup(for_target=self._name())
                    )
                except Exception as e:
                    self._log("WARN try_dismiss_popup failed: %r", e)
                dismiss_rounds += 1

                if dismissed:
                    any_dismiss_cleared = True
                    if not quiet:
                        self._log(
                            "target missing; dismissed popup, retry target after "
                            "%.2fs (poll >= %.2fs, re-dismiss in %.2fs) file=%s",
                            d_delay,
                            post_poll,
                            re_dismiss_gap,
                            self.qb_resolved,
                        )
                    # Poll target for post_poll; re-probe dismiss list soon so
                    # stacked Next Stage does not wait the full target window.
                    deadline, dismiss_after = _after_dismiss_extend()
                    continue

                if not quiet:
                    self._log(
                        "target missing; dismiss list (next_stage/ok/close) "
                        "all absent file=%s",
                        self.qb_resolved,
                    )
                # No popup this round — back off briefly; if we already cleared
                # a layer, keep the extended deadline (stacked panel may lag).
                if any_dismiss_cleared:
                    dismiss_after = now + max(interval, re_dismiss_gap)
                else:
                    dismiss_after = now + max(interval, pre_grace * 0.5)

            # wait=None and never extended by a dismiss: still honor pre-dismiss
            # grace so we retry the target a few times before giving up / dismissing.
            if deadline is None:
                if do_dismiss and now < (search_start + pre_grace):
                    time.sleep(interval)
                    continue
                # One-shot path: grace over, dismiss tried (or disabled) → stop
                break

            if time.time() >= deadline:
                break
            time.sleep(interval)

        if xy is None:
            if not quiet:
                self._log(
                    "not found (threshold=%s wait=%s strategy=%s required=%s "
                    "which=%s dismiss_popups=%s path=%s)",
                    self.qb_threshold,
                    w,
                    self.qb_cv_strategy,
                    must,
                    which_eff,
                    do_dismiss,
                    self.qb_resolved,
                )
            if must:
                raise RuntimeError(
                    "NewTemplate image not found (required=True): %s (%s)"
                    % (self._name(), self.qb_resolved)
                )
            return None

        if not quiet:
            self._log(
                "found pos=(%s,%s) which=%s path=%s",
                xy[0],
                xy[1],
                which_eff,
                self.qb_resolved,
            )
        return xy

    def _click_xy(self, xy, times=1):
        """Click device/window coords from a match — no second image search."""
        from airtest.core.api import touch as air_touch

        x, y = xy
        for _ in range(max(1, int(times))):
            air_touch((x, y))
        self._log("click pos=(%s,%s) times=%s", x, y, times)

    def _touch_click_recover_popups(
        self,
        xy,
        times=1,
        which=None,
        which_eff=None,
        do_dismiss=True,
        dismiss_popups=None,
        dismiss_retry_delay=None,
        wait=None,
        required=None,
        raise_on_miss=None,
        re_touch=True,
    ):
        """Click target; recover from next_stage/ok overlays only.

        Critical: one-shot controls (Sanctum chests, etc.) **change art** after
        a successful click. If the target is gone after click, that is success
        — never re-raise ``required`` on a re-find miss after we already
        clicked. If next_stage/ok is still up, **drain** those overlays first
        so the next step (e.g. Battle) is not blocked by stacked result panels.

        - next_stage/ok + target still matches → dismiss, re-touch (occluded)
          unless ``re_touch=False`` (one intentional click only)
        - next_stage/ok + target gone → drain overlays; if target reappears
          re-touch (hub navigation miss) unless ``re_touch=False``; else success
        - Close co-visible → ignore (dialog chrome; Collect+Close)

        ``re_touch=False`` still clears overlays **before** the first click
        (popups covering the control). It only disables **post-click** clicks
        on the same target (Arena #1 row still visible under result OK).
        """
        if not do_dismiss:
            self._click_xy(xy, times=times)
            return True

        try:
            from .common import (
                is_dismiss_image_name,
                overlay_blocking,
                try_dismiss_popup,
            )
        except Exception as e:
            self._log("WARN popup recover import failed: %r — single click", e)
            self._click_xy(xy, times=times)
            return True

        if is_dismiss_image_name(self._name()):
            self._click_xy(xy, times=times)
            return True

        allow_re_touch = bool(re_touch)
        max_rounds = 1 + max(0, int(_POST_CLICK_POPUP_RECOVERIES))
        cur_xy = xy
        clicked_once = False
        last_blocker = None

        # --- re_touch=False: clear overlays before click, then one target tap ---
        if not allow_re_touch:
            pre_budget = 1 + max(0, int(_POST_CLICK_POPUP_RECOVERIES))
            for pre_i in range(1, pre_budget + 1):
                try:
                    pre_block = overlay_blocking(for_target=self._name())
                except Exception as e:
                    self._log("WARN pre-click overlay_blocking failed: %r", e)
                    pre_block = None
                if pre_block is None:
                    break
                self._log(
                    "pre-click: overlay %s — dismiss before single-touch "
                    "(round %s) file=%s",
                    pre_block,
                    pre_i,
                    self.qb_resolved,
                )
                try:
                    try_dismiss_popup(for_target=self._name())
                except Exception as e:
                    self._log("WARN pre-click try_dismiss_popup failed: %r", e)
                re_wait = (
                    float(_POST_DISMISS_POLL_S)
                    if wait is None
                    else max(float(wait), float(_POST_DISMISS_POLL_S))
                )
                new_xy = self.find(
                    wait=re_wait,
                    do_pause=False,
                    required=False,
                    which=which,
                    dismiss_popups=True,
                    dismiss_retry_delay=dismiss_retry_delay,
                )
                if new_xy is None:
                    self._log(
                        "pre-click: target missing after overlay dismiss "
                        "(re_touch=False) file=%s",
                        self.qb_resolved,
                    )
                    must = self._resolve_required(
                        required=required, raise_on_miss=raise_on_miss
                    )
                    if must:
                        raise RuntimeError(
                            "NewTemplate image not found after pre-click overlay "
                            "dismiss (required=True): %s (%s)"
                            % (self._name(), self.qb_resolved)
                        )
                    return False
                cur_xy = new_xy
            self._log(
                "click single (re_touch=False) pos=(%s,%s) file=%s",
                cur_xy[0],
                cur_xy[1],
                self.qb_resolved,
            )
            self._click_xy(cur_xy, times=times)
            if _POST_CLICK_SETTLE_S > 0:
                time.sleep(float(_POST_CLICK_SETTLE_S))
            self._log(
                "post-click: re_touch=False — done (no recover re-touch) file=%s",
                self.qb_resolved,
            )
            return True

        # --- default: full post-click recover / re-touch under overlay ---
        for round_i in range(1, max_rounds + 1):
            try:
                pre_block = overlay_blocking(for_target=self._name())
            except Exception as e:
                self._log("WARN pre-click overlay_blocking failed: %r", e)
                pre_block = None

            if pre_block is not None:
                self._log(
                    "pre-click: overlay %s — dismiss before touch (round %s) "
                    "file=%s",
                    pre_block,
                    round_i,
                    self.qb_resolved,
                )
                try:
                    try_dismiss_popup(for_target=self._name())
                except Exception as e:
                    self._log("WARN pre-click try_dismiss_popup failed: %r", e)
                re_wait = (
                    float(_POST_DISMISS_POLL_S)
                    if wait is None
                    else max(float(wait), float(_POST_DISMISS_POLL_S))
                )
                new_xy = self.find(
                    wait=re_wait,
                    do_pause=False,
                    required=False,
                    which=which,
                    dismiss_popups=True,
                    dismiss_retry_delay=dismiss_retry_delay,
                )
                if new_xy is None:
                    # Only treat as success if we already clicked this control;
                    # pre-click miss with no prior click is a real failure.
                    if clicked_once:
                        self._log(
                            "pre-click: target gone after dismiss (already "
                            "clicked) — success file=%s",
                            self.qb_resolved,
                        )
                        return True
                    self._log(
                        "pre-click: target missing after overlay dismiss "
                        "(round %s) file=%s",
                        round_i,
                        self.qb_resolved,
                    )
                    must = self._resolve_required(
                        required=required, raise_on_miss=raise_on_miss
                    )
                    if must:
                        raise RuntimeError(
                            "NewTemplate image not found after pre-click overlay "
                            "dismiss (required=True): %s (%s)"
                            % (self._name(), self.qb_resolved)
                        )
                    return False
                cur_xy = new_xy
                continue

            self._log(
                "click recover-round %s/%s pos=(%s,%s) pre_block=%s",
                round_i,
                max_rounds,
                cur_xy[0],
                cur_xy[1],
                pre_block,
            )
            self._click_xy(cur_xy, times=times)
            clicked_once = True
            if _POST_CLICK_SETTLE_S > 0:
                time.sleep(float(_POST_CLICK_SETTLE_S))

            try:
                last_blocker = overlay_blocking(for_target=self._name())
            except Exception as e:
                self._log("WARN post-click overlay_blocking failed: %r", e)
                return True

            if last_blocker is None:
                if round_i > 1:
                    self._log(
                        "post-click: no overlay after touch (round %s) — success "
                        "file=%s",
                        round_i,
                        self.qb_resolved,
                    )
                return True

            # Overlay still up. If target art is already gone, either the click
            # navigated / changed art (chest), or a victory panel covered it.
            # Drain ok/next_stage first; if the target **reappears**, the click
            # did not land (or was undone) — re-touch instead of false success.
            still_xy = None
            try:
                still_xy = self._snapshot_match_once(
                    which=which_eff if which_eff is not None else which
                )
            except Exception as e:
                self._log("WARN post-click re-match failed: %r", e)
                still_xy = None
            if still_xy is None:
                self._log(
                    "post-click: target gone after click (overlay=%s still "
                    "up) — drain overlays then verify file=%s",
                    last_blocker,
                    self.qb_resolved,
                )
                drain_n = max(1, int(_POST_CLICK_DRAIN_ROUNDS))
                for drain_i in range(1, drain_n + 1):
                    try:
                        # quick=True: do not sit on empty rescans — next script
                        # step may be time-sensitive (SkipAll during battle).
                        drained = bool(
                            try_dismiss_popup(
                                for_target=self._name(), quick=True
                            )
                        )
                    except Exception as e:
                        self._log(
                            "WARN post-click drain try_dismiss failed: %r", e
                        )
                        break
                    if not drained:
                        if drain_i > 1:
                            self._log(
                                "post-click: overlay drain empty after %s "
                                "round(s) file=%s",
                                drain_i - 1,
                                self.qb_resolved,
                            )
                        break
                    self._log(
                        "post-click: drained overlay layer %s/%s file=%s",
                        drain_i,
                        drain_n,
                        self.qb_resolved,
                    )
                # After drain: hub rows (Ruins) often reappear if the click
                # never entered the child screen — re-click rather than success.
                reappear_xy = None
                try:
                    reappear_xy = self._snapshot_match_once(
                        which=which_eff if which_eff is not None else which
                    )
                except Exception as e:
                    self._log("WARN post-click reappear check failed: %r", e)
                    reappear_xy = None
                if reappear_xy is not None:
                    # Hub rows (Ruins / realm tabs): reappear usually means the
                    # click never entered the child screen → re-touch.
                    #
                    # IdleBattle / SkipAll: reappear after next_stage/ok drain
                    # usually means multi-stage finished or combat chrome
                    # returned — re-touching spams idle/skip (S8).
                    #
                    # Battle.png is different (Realms cddd5fc3… EndlessDesert):
                    # a Next Stage stack can dim the real Battle; the raw click
                    # hits the overlay, drain clears Next Stage, Battle
                    # reappears, and "success without re-touch" skips the fight.
                    # Prefer re-touch Battle; pair with confirm=IdleBattle on
                    # formation Battle in multi-stage scripts when possible.
                    name_l = (self._name() or "").lower()
                    if name_l in (
                        "idlebattle.png",
                        "skipall.png",
                    ):
                        self._log(
                            "post-click: %s reappeared after overlay drain at "
                            "pos=(%s,%s) — success without re-touch "
                            "(multi-stage/result, not missed hub nav) file=%s",
                            name_l,
                            reappear_xy[0],
                            reappear_xy[1],
                            self.qb_resolved,
                        )
                        return True
                    self._log(
                        "post-click: target reappeared after overlay drain at "
                        "pos=(%s,%s) — re-touch (navigation click missed"
                        "%s) file=%s",
                        reappear_xy[0],
                        reappear_xy[1],
                        (
                            "; Battle may have been under Next Stage"
                            if name_l == "battle.png"
                            else ""
                        ),
                        self.qb_resolved,
                    )
                    cur_xy = reappear_xy
                    continue
                self._log(
                    "post-click: target still gone after drain — success "
                    "(one-shot / navigated) file=%s",
                    self.qb_resolved,
                )
                return True

            self._log(
                "post-click: overlay %s + target still matched (round %s/%s); "
                "dismiss then re-touch file=%s",
                last_blocker,
                round_i,
                max_rounds,
                self.qb_resolved,
            )
            try:
                try_dismiss_popup(for_target=self._name())
            except Exception as e:
                self._log("WARN post-click try_dismiss_popup failed: %r", e)

            re_wait = (
                float(_POST_DISMISS_POLL_S)
                if wait is None
                else max(float(wait), float(_POST_DISMISS_POLL_S))
            )
            new_xy = self.find(
                wait=re_wait,
                do_pause=False,
                required=False,
                which=which,
                dismiss_popups=True,
                dismiss_retry_delay=dismiss_retry_delay,
            )
            if new_xy is None:
                # Already clicked; target consumed after clearing overlay.
                self._log(
                    "post-click: target not found after dismiss (round %s) "
                    "but click already done — success (one-shot control) "
                    "file=%s",
                    round_i,
                    self.qb_resolved,
                )
                return True
            cur_xy = new_xy
            self._log(
                "post-click: re-found target at pos=(%s,%s); will re-touch",
                cur_xy[0],
                cur_xy[1],
            )

        self._log(
            "WARN post-click: still seeing overlay %s after %s recoveries "
            "file=%s",
            last_blocker,
            max_rounds,
            self.qb_resolved,
        )
        # We did click at least once.
        return True

    def touch(
        self,
        times=1,
        wait=None,
        required=None,
        raise_on_miss=None,
        which=None,
        dismiss_popups=None,
        dismiss_retry_delay=None,
        confirm=None,
        confirm_isCommon=None,
        confirm_pause=None,
        confirm_timeout=None,
        confirm_retries=None,
        confirm_change=None,
        re_touch=None,
        **kwargs
    ):
        """pause → find (with optional popup dismiss list) → click.

        Returns **True** if clicked, **False** if not found.

        When ``dismiss_popups`` is True (default False)::

            if target missing: after grace, walk next_stage → ok → close
            if target found under next_stage/ok: clear overlay, re-find
            co-visible Close with found target is not auto-dismissed
            after click: re-touch only if overlay remains and target still
            matches (unless ``re_touch=False``); target gone = success

        ``re_touch=False``: still dismiss popups during **find** / pre-click;
        after the first intentional target click, do **not** click the target
        again (use for list rows that stay visible under result OK).

        Post-click confirmation (lag / unregistered click)::

            # Preferred: wait for a known UI marker after the click
            NewTemplate("Battle.png", isCommon=True).click(confirm="IdleBattle.png")
            NewTemplate(r"..\\\\Go.png").touch(
                confirm=r"..\\\\Done.png",
                confirm_pause=1.0,
                confirm_timeout=5,
                confirm_retries=2,
            )
            # Any of several markers:
            .touch(confirm=["OK.png", "NextStage.png"], confirm_isCommon=True)

            # Optional weak fallback (no marker image): pixel major-change
            .touch(confirm_change=True)

        If ``confirm`` is set: after each click, sleep ``confirm_pause``, then
        poll for that image; if missing within ``confirm_timeout``, re-tap the
        same xy (up to confirm_retries).

        Parameters
        ----------
        times : int
            How many clicks at the match position (each attempt).
        wait : float or None
            Override instance wait timeout.
        required : bool or None
            If True, missing image raises. None → constructor ``required``.
        which : str or None
            Override which match (e.g. ``"bottommiddle"``).
        dismiss_popups : bool or None
            Override auto popup dismiss list.
        dismiss_retry_delay : float or None
            After a successful popup dismiss, wait this many seconds before
            re-searching the target (default 0.8).
        confirm : str / NewTemplate / sequence / None
            Image(s) that must appear after a successful click.
        confirm_isCommon : bool or None
            Load confirm basenames from screens/Common/.
        confirm_pause : float or None
            Fixed delay after click before searching for confirm (default 0.25).
        confirm_timeout : float or None
            Poll window for confirm image after ``confirm_pause``.
        confirm_retries : int or None
            Extra re-clicks if confirm never appears (default 2).
        confirm_change : bool or None
            If True and no ``confirm`` image, use pixel major-change fallback.
            Any active confirm (image or pixel) raises if never satisfied.
        re_touch : bool or None
            None → constructor ``re_touch`` (default True). False → one target
            click only after find (no post-click re-fire of the same control).
        raise_on_miss : bool or None
            Alias for ``required``.
        """
        if kwargs:
            print(
                "[NewTemplate] note: ignoring extra touch kwargs %s" % (list(kwargs),),
                file=sys.stderr,
            )

        which_eff = self._normalize_which(self.qb_which if which is None else which)

        # Resolve confirm image list (call-site overrides constructor)
        conf_spec = self.qb_confirm if confirm is None else confirm
        conf_is_common = (
            self.qb_confirm_is_common
            if confirm_isCommon is None
            else confirm_isCommon
        )
        conf_items = self._normalize_confirm_list(conf_spec)
        # Temporarily apply confirm_isCommon override for builders
        old_c = self.qb_confirm_is_common
        if confirm_isCommon is not None:
            self.qb_confirm_is_common = confirm_isCommon
        try:
            conf_tpls = [
                self._make_confirm_template(c, confirm_isCommon=conf_is_common)
                for c in conf_items
            ]
        finally:
            self.qb_confirm_is_common = old_c

        do_pixel = (
            self.qb_confirm_change
            if confirm_change is None
            else bool(confirm_change)
        )
        # Image confirm wins; pixel only if no confirm images
        use_image_confirm = bool(conf_tpls)
        use_pixel_confirm = bool(do_pixel) and not use_image_confirm

        if confirm_pause is not None:
            p_conf = max(0.0, float(confirm_pause))
        else:
            p_conf = float(self.qb_confirm_pause)

        t_conf = (
            float(self.qb_confirm_timeout)
            if confirm_timeout is None
            else float(confirm_timeout)
        )
        n_retry = (
            int(self.qb_confirm_retries)
            if confirm_retries is None
            else max(0, int(confirm_retries))
        )

        do_dismiss = (
            self.qb_dismiss_popups
            if dismiss_popups is None
            else bool(dismiss_popups)
        )
        do_re_touch = self.qb_re_touch if re_touch is None else bool(re_touch)
        self._log(
            "touch start thr=%.2f wait=%s which=%s dismiss=%s re_touch=%s "
            "times=%s file=%s",
            self.qb_threshold,
            self.qb_wait if wait is None else wait,
            which_eff,
            do_dismiss,
            do_re_touch,
            times,
            self.qb_resolved,
        )

        xy = self.find(
            wait=wait,
            do_pause=True,
            required=required,
            raise_on_miss=raise_on_miss,
            which=which,
            dismiss_popups=dismiss_popups,
            dismiss_retry_delay=dismiss_retry_delay,
        )
        if xy is None:
            self._log("touch miss (not clicked) file=%s", self.qb_resolved)
            return False

        conf_names = [t._name() for t in conf_tpls] if conf_tpls else []
        if use_image_confirm:
            missing = [
                t for t in conf_tpls if not os.path.isfile(getattr(t, "qb_resolved", ""))
            ]
            self._log(
                "will confirm click with image(s)=%s pause=%.2fs timeout=%.2fs retries=%s",
                conf_names,
                p_conf,
                t_conf,
                n_retry,
            )
            if missing and len(missing) == len(conf_tpls):
                # All confirm files missing — do not re-click; fail hard
                self._log(
                    "all confirm images missing on disk %s — single click then raise",
                    [t.qb_resolved for t in missing],
                )
                self._click_xy(xy, times=times)
                raise RuntimeError(
                    "NewTemplate[%s] confirm image(s) missing on disk: %s"
                    % (self._name(), [t.qb_resolved for t in missing])
                )

        if not use_image_confirm and not use_pixel_confirm:
            return self._touch_click_recover_popups(
                xy,
                times=times,
                which=which,
                which_eff=which_eff,
                do_dismiss=do_dismiss,
                dismiss_popups=dismiss_popups,
                dismiss_retry_delay=dismiss_retry_delay,
                wait=wait,
                required=required,
                raise_on_miss=raise_on_miss,
                re_touch=do_re_touch,
            )

        attempts = 1 + n_retry
        last_detail = None
        for attempt in range(1, attempts + 1):
            pre_name = None
            before = None

            # Dimmed under next_stage/ok: clear before clicking (Back+confirm).
            if do_dismiss:
                try:
                    from .common import overlay_blocking, try_dismiss_popup

                    ov = overlay_blocking(for_target=self._name())
                    if ov is not None:
                        self._log(
                            "confirm pre-click: overlay %s — dismiss before "
                            "attempt %s file=%s",
                            ov,
                            attempt,
                            self.qb_resolved,
                        )
                        try_dismiss_popup(for_target=self._name())
                        re_wait = (
                            float(_POST_DISMISS_POLL_S)
                            if wait is None
                            else max(float(wait), float(_POST_DISMISS_POLL_S))
                        )
                        new_xy = self.find(
                            wait=re_wait,
                            do_pause=False,
                            required=False,
                            which=which,
                            dismiss_popups=True,
                            dismiss_retry_delay=dismiss_retry_delay,
                        )
                        if new_xy is not None:
                            xy = new_xy
                        else:
                            self._log(
                                "confirm pre-click: target missing after "
                                "overlay dismiss attempt=%s file=%s",
                                attempt,
                                self.qb_resolved,
                            )
                except Exception as e:
                    self._log("WARN confirm pre-click overlay dismiss: %r", e)

            if use_image_confirm:
                pre_name = self._any_confirm_present(conf_tpls)
                if pre_name is not None:
                    self._log(
                        "confirm image already on screen pre-click: %s "
                        "(also require this target to leave)",
                        pre_name,
                    )
            if use_pixel_confirm:
                before = self._snapshot_screen()

            self._log("click attempt %s/%s", attempt, attempts)
            self._click_xy(xy, times=times)

            if use_image_confirm:
                pre_present = pre_name is not None
                ok, last_detail = self._wait_for_confirm_images(
                    conf_tpls,
                    timeout=t_conf,
                    pre_present=pre_present,
                    which=which_eff,
                    confirm_pause=p_conf,
                )
                if ok:
                    self._log(
                        "CONFIRMED attempt=%s/%s via confirm=%s",
                        attempt,
                        attempts,
                        last_detail,
                    )
                    return True
                self._log(
                    "confirm NOT seen attempt=%s/%s looking_for=%s "
                    "(dismiss overlay if any, recheck confirm, then "
                    "re-find+re-click if retries left)",
                    attempt,
                    attempts,
                    last_detail,
                )
                # S7: click may already have navigated (target gone) while a
                # late next_stage/ok blocked the confirm marker during the
                # poll. Clear overlay, recheck confirm *before* re-find/re-tap
                # of the click target (re-tapping Back after success is wrong).
                saw_overlay = False
                if do_dismiss:
                    try:
                        from .common import overlay_blocking, try_dismiss_popup

                        ov = overlay_blocking(for_target=self._name())
                        if ov is not None:
                            saw_overlay = True
                            self._log(
                                "confirm retry: overlay %s after failed "
                                "confirm — try_dismiss_popup file=%s",
                                ov,
                                self.qb_resolved,
                            )
                            try_dismiss_popup(for_target=self._name())
                    except Exception as e:
                        self._log("WARN confirm-retry overlay dismiss: %r", e)

                # Longer poll when we just cleared chrome; short settle otherwise.
                recheck_t = (
                    float(t_conf)
                    if saw_overlay
                    else min(0.5, max(0.05, float(t_conf)))
                )
                ok2, detail2 = self._wait_for_confirm_images(
                    conf_tpls,
                    timeout=recheck_t,
                    pre_present=pre_present,
                    which=which_eff,
                    confirm_pause=0.0,
                )
                if ok2:
                    self._log(
                        "CONFIRMED attempt=%s/%s via post-dismiss recheck "
                        "confirm=%s (no re-touch)",
                        attempt,
                        attempts,
                        detail2,
                    )
                    return True
                if detail2 is not None:
                    last_detail = detail2

                # Stale xy after a navigation that moved us: re-locate target
                # before the next attempt (only if confirm still missing).
                if attempt < attempts:
                    new_xy = self.find(
                        wait=None,
                        do_pause=False,
                        required=False,
                        which=which,
                        dismiss_popups=dismiss_popups,
                        dismiss_retry_delay=dismiss_retry_delay,
                    )
                    if new_xy is not None:
                        xy = new_xy
                        self._log(
                            "confirm retry: re-found target at pos=(%s,%s)",
                            xy[0],
                            xy[1],
                        )
                    else:
                        self._log(
                            "confirm retry: target not on screen after failed "
                            "confirm — stop re-clicking stale pos"
                        )
                        break
                continue

            # pixel fallback
            ok, reason, stats = self._wait_for_click_effect(
                before,
                which=which_eff,
                timeout=t_conf,
                confirm_pause=p_conf,
            )
            last_detail = (reason, stats)
            if ok:
                self._log(
                    "CONFIRMED attempt=%s/%s via pixel reason=%s",
                    attempt,
                    attempts,
                    reason,
                )
                return True
            self._log(
                "no major change attempt=%s/%s reason=%s (re-click if retries left)",
                attempt,
                attempts,
                reason,
            )

        msg = (
            "click not confirmed after %s attempt(s) looking_for=%s detail=%s"
            % (attempts, conf_names or "(pixel)", last_detail)
        )
        raise RuntimeError("NewTemplate[%s] %s" % (self._name(), msg))

    def click(self, *args, **kwargs):
        """Alias for :meth:`touch` (find → click → optional confirm image)."""
        return self.touch(*args, **kwargs)

    def exists(self, wait=None, do_pause=False, which=None, dismiss_popups=False):
        """Return True if image is on screen (no click).

        Does **not** apply ``pause`` by default (set ``do_pause=True`` to sleep first).
        Safe against OpenCV errors — returns False instead of crashing.
        Never raises (always optional check).

        Default ``dismiss_popups=False`` so a pure check does not click OK/close.
        Logs target filename unless ``_qb_quiet_find`` is set (internal probes).
        """
        quiet = bool(getattr(self, "_qb_quiet_find", False))
        if do_pause:
            self._do_pause()
        which_eff = self._normalize_which(self.qb_which if which is None else which)
        w = self.qb_wait if wait is None else float(wait)
        if w is not None and w <= 0:
            w = None
        if not quiet:
            self._log(
                "exists start thr=%.2f wait=%s which=%s file=%s",
                self.qb_threshold,
                w,
                which_eff,
                self.qb_resolved,
            )
        if not os.path.isfile(self.qb_resolved):
            if not quiet:
                self._log("exists False (file missing) file=%s", self.qb_resolved)
            return False
        if w is None:
            ok = self._snapshot_match_once(which=which_eff) is not None
            if not quiet:
                self._log(
                    "exists %s file=%s",
                    "True" if ok else "False",
                    self.qb_resolved,
                )
            return ok
        deadline = time.time() + float(w)
        while True:
            if self._snapshot_match_once(which=which_eff) is not None:
                if not quiet:
                    self._log("exists True file=%s", self.qb_resolved)
                return True
            if time.time() >= deadline:
                if not quiet:
                    self._log("exists False (wait elapsed) file=%s", self.qb_resolved)
                return False
            time.sleep(0.5)
