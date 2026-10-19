# -*- coding: utf-8 -*-
"""Common HUD controls under screens/Common/ (Back, OK, Close, …).

**Before changing dismiss / overlay / popup behavior:** re-read
``docs/dismiss-popup-scenarios.md`` **§0 Frozen requirements** (R1–R13)
and **§1.5 Script authoring** (one owner for chrome).
Do not flip Next Stage vs OK policy or Close-as-overlay rules without
explicit operator approval.

One import for everything::

    from _lib import common

    common.back()
    common.try_dismiss_popup()       # clear stacked next_stage/ok/close if needed
    # Confirm = image that should appear after the click (page-local by default):
    common.back(confirm="Quest-Explore-Realms.png")  # parent/.air then one up
    common.battle(confirm="IdleBattle.png", confirm_isCommon=True)  # Common HUD

**Script authoring (default):** do **not** call ``common.ok()`` / ``common.close()``
for result, claim, or promo chrome. Default ``dismiss_popups=True`` on real
controls already walks **next_stage → ok → close**. Explicit ok/close use
``dismiss_popups=False`` (raw single tap) and **race** the dismiss stack —
double handling, wrong layer, S6 order bugs. Prefer only non-chrome actions
(tabs, rows, chests, Collect, Battle, Back). See
``docs/dismiss-popup-scenarios.md`` §1.5.

Legacy helpers still exist (S6 guard on ``ok()`` for old macros)::

    common.ok()       # prefer avoid in new scripts
    common.close()    # prefer avoid in new scripts
    common.next_stage()  # multi-stage *continue* when you must name it

Click targets use isCommon=True → screens/Common/<name>.png.
Confirm images default to **parent of .air**, then **one folder above** (not Common).

``which`` (passed to NewTemplate): ``best`` or relative 3×3 among match hits
(not full-screen — works when buttons are close)::

    topleft      topmiddle      topright
    middleleft   center         middleright
    bottomleft   bottommiddle   bottomright

Popup stack: NewTemplate.touch(dismiss_popups=True) calls ``try_dismiss_popup``
when the target is **missing** (after grace), or when the target is found
but **next_stage/ok** is also on screen (dimmed HUD under a victory/result
overlay). Co-visible **Close** is not treated as an overlay — Collect +
Close on a reward panel must not auto-close instead of Collect.

Stacked identical dismiss buttons are still cleared (same or moved pos).

Default dismiss order: **next_stage → ok → close**.
Overlay-only names (target visible): **next_stage → ok**.

**INVARIANT — Next Stage popup never uses OK:** if NextStage is visible
(dual victory Back/OK/Next Stage, or next_stage alone), auto-dismiss and
``common.ok()`` must press **next_stage only** — never OK (OK aborts
multi-stage). OK is only used when Next Stage is **not** on screen.

**S10 — Close over dimmed Next Stage:** Next Stage panels do not have Close.
If next_stage stays at the same pos after a few taps and Close co-matches,
``try_dismiss_popup`` escalates to Close (still never OK).

``common.ok()`` S6 guard: if NextStage is visible, press **next_stage** instead
of OK. Auto-dismiss uses the same rule (order + dual-panel skip OK).

**INVARIANT — dismiss path vs public helpers**

``try_dismiss_popup`` / ``popup_blocking`` / ``_dismiss_template`` must **never**
call ``ok()``, ``close()``, or ``next_stage()``. They only use
``_dismiss_template(...).touch(dismiss_popups=False)`` (raw NewTemplate).

Reasons:

- ``ok()`` has an S6 NextStage redirect; using it from the stack walk would
  couple dismiss order to script helpers and can skip or re-order layers.
- Public helpers must not re-enter ``try_dismiss_popup`` while clearing a stack
  (``dismiss_popups=False`` on dismiss templates and on ok/close/next_stage).
"""
from __future__ import print_function

import os
import sys
import time

from .actions import NewTemplate
from .templates import is_hard_system_error

# --- Promoted Common assets (screens/Common/) ---
IMG_BACK = "Back-lower-left.png"
IMG_OK = "OK.png"
IMG_CLOSE = "Close-small.png"
IMG_BATTLE = "Battle.png"
IMG_IDLE_BATTLE = "IdleBattle.png"
IMG_NEXT_STAGE = "NextStage.png"
IMG_SKIP_ALL = "SkipAll.png"
IMG_THUMB_UP = "ThumbUp.png"
IMG_COLLECT = "Collect.png"
IMG_FREE = "Free.png"
IMG_EXCLAIM = "Exclaim.png"

# Dismiss list when target missing or occluded by a modal (first found is clicked).
# Prefer **next_stage** over **ok**: dual victory panels show both; OK aborts
# multi-stage. If Next Stage is visible, never press OK (see try_dismiss_popup).
DISMISS_ORDER = ("next_stage", "ok", "close")
_DISMISS_IMAGES = {
    "close": IMG_CLOSE,
    "next_stage": IMG_NEXT_STAGE,
    "ok": IMG_OK,
}
# Basenames of dismiss controls — skip "popup blocking me" logic when the
# *target itself* is one of these (ok()/close()/next_stage() must still click).
_DISMISS_BASENAMES = frozenset(_DISMISS_IMAGES.values())
# Semi-transparent victory / result overlays that dim HUD underneath.
# Close is *not* here: reward dialogs often show Collect + Close together;
# auto-tapping Close closes the dialog instead of Collect. Close is only
# used when the *target is missing* (try_dismiss_popup full list).
# Order for probes follows DISMISS_ORDER (next_stage before ok).
OVERLAY_DISMISS_NAMES = frozenset(("next_stage", "ok"))

# Defaults when public helpers pass confirm_*=None
_DEFAULT_CONFIRM_PAUSE = 0.8
_DEFAULT_CONFIRM_TIMEOUT = 2.0

# try_dismiss_popup — stacked popups with the same button art:
#   1) same position  → re-tap until gone (or budget exhausted)
#   2) different pos  → match center moves → front layer cleared; keep going
#   3) next_stage sticky + co-visible Close → escalate to Close (S10).
#      Next Stage panels only have OK (not Close). Close co-matching while
#      Next Stage still scores is a *separate* promo/X on top of a dimmed
#      Next Stage — re-tapping Next Stage forever does nothing. Still never
#      pick OK while Next Stage is visible (R1/S6).
_DISMISS_GONE_PAUSE_S = 0.35  # fixed delay after click before first check
_DISMISS_GONE_TIMEOUT_S = 1.0  # poll window for "button gone" / moved
_DISMISS_GONE_POLL_S = 0.2
_DISMISS_MOVED_PX = 28  # match center shift → different-pos stack layer
_DISMISS_MAX_LAYERS = 8  # max dismiss taps per try_dismiss_popup call
# After this many same-pos next_stage taps that did not clear, if Close is
# also visible, pick Close next (S10). 2 ⇒ try Next Stage thrice total
# (initial + one re-tap + escalate on third pick after 2 stickies).
_DISMISS_NEXT_STAGE_STICKY_BEFORE_CLOSE = 2
# Before allowing a lone OK, re-probe Next Stage at this floor (S6).
# "Move to the next stage" / dual panels sometimes score NextStage just
# under the normal dismiss threshold while OK still matches — falling
# through to OK aborts multi-stage (Realms log 2026-07-31).
_DISMISS_NEXT_STAGE_SOFT_THR = 0.75
# After a layer is fully gone, wait for the next stacked panel to paint
# (victory → next victory / reward animation). Keep modest — long waits here
# block the *next* script step (e.g. battle → SkipAll) and miss time-sensitive
# buttons. Stacked multi-stage still uses empty rescans below.
_DISMISS_POST_GONE_S = 0.55
# When a layer just cleared but no dismiss button is visible yet, re-scan
# a few times before concluding the stack is empty (transition frames).
# Full mode: enough for Realms multi-NextStage; quick mode (post-click drain)
# uses the smaller budget so Battle→SkipAll is not starved.
_DISMISS_EMPTY_RESCANS = 4
_DISMISS_EMPTY_RESCAN_S = 0.35
_DISMISS_EMPTY_RESCANS_QUICK = 1
_DISMISS_EMPTY_RESCAN_S_QUICK = 0.2
_DISMISS_POST_GONE_S_QUICK = 0.25
# Slightly below common.close() default (0.84): shop/forge closes often sit ~0.83–0.87
_DISMISS_THRESHOLD = 0.83

__all__ = [
    "back",
    "ok",
    "close",
    "battle",
    "idle_battle",
    "next_stage",
    "skip_all",
    "thumb_up",
    "collect",
    "free",
    "exclaim",
    "try_dismiss_popup",
    "popup_blocking",
    "overlay_blocking",
    "is_dismiss_image_name",
    "DISMISS_ORDER",
    "OVERLAY_DISMISS_NAMES",
    "IMG_BACK",
    "IMG_OK",
    "IMG_CLOSE",
    "IMG_BATTLE",
    "IMG_IDLE_BATTLE",
    "IMG_NEXT_STAGE",
    "IMG_SKIP_ALL",
    "IMG_THUMB_UP",
    "IMG_COLLECT",
    "IMG_FREE",
    "IMG_EXCLAIM",
]


def _tap(
    filename,
    pause=0.5,
    threshold=0.85,
    required=True,
    wait=None,
    times=1,
    which="best",
    dismiss_popups=False,
    dismiss_retry_delay=None,
    confirm=None,
    confirm_isCommon=False,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
    confirm_change=False,
):
    """Shared NewTemplate.touch for Common HUD images.

    dismiss_popups defaults False so dismiss helpers never recurse into
    try_dismiss_popup while clearing a stack.

    dismiss_retry_delay: after a popup dismiss, wait before re-finding
    the target (when dismiss_popups=True on the caller).

    confirm: image that must appear after the click. Default search order
      (see ``resolve_template_path``): parent of .air → one folder above →
      .air folder last → Common. Prefer hub paths for page art, e.g.
      confirm=r\"..\\\\..\\\\Home-LimitedSummon_000_59_471.png\".
      Bare names are OK when the file lives on the page/hub (not a stale
      copy inside .air). Use confirm_isCommon=True only for screens/Common/.
      When set, missing confirm **raises** after retries (script/chain aborts).
    confirm_pause / confirm_timeout: None → common defaults (0.8s / 2s).
    Confirm match threshold is capped (~0.75) so soft page markers are not
    held to the HUD click threshold (Back 0.84 etc.).
    """
    if confirm_pause is None:
        confirm_pause = _DEFAULT_CONFIRM_PAUSE
    if confirm_timeout is None:
        confirm_timeout = _DEFAULT_CONFIRM_TIMEOUT
    return NewTemplate(
        filename,
        threshold=threshold,
        pause=pause,
        wait=wait,
        isCommon=True,
        required=required,
        which=which,
        dismiss_popups=dismiss_popups,
        dismiss_retry_delay=dismiss_retry_delay,
        confirm=confirm,
        confirm_isCommon=bool(confirm_isCommon),
        confirm_pause=float(confirm_pause),
        confirm_timeout=float(confirm_timeout),
        confirm_retries=confirm_retries,
        confirm_change=confirm_change,
    ).touch(times=times)


def back(
    pause=1,
    threshold=0.84,
    required=True,
    wait=None,
    times=1,
    which="best",
    dismiss_retry_delay=None,
    confirm=None,
    confirm_isCommon=False,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    """Lower-left Back (screens/Common/Back-lower-left.png).

    Easy confirm (page-local, not Common)::

        common.back(confirm=\"Quest-Explore-Realms.png\")
        # looks in parent of .air, then one folder above;
        # missing confirm raises (hard fail) after retries
    """
    return _tap(
        IMG_BACK,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        dismiss_retry_delay=dismiss_retry_delay,
        confirm=confirm,
        confirm_isCommon=confirm_isCommon,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )


def _next_stage_visible(threshold=None):
    """True if Common NextStage.png matches (quiet probe; no click)."""
    if threshold is None:
        threshold = _DISMISS_THRESHOLD
    tpl = _dismiss_template("next_stage", threshold=float(threshold), pause=0.0)
    if tpl is None:
        return False
    try:
        return _find_pos_quiet(tpl) is not None
    except Exception as e:
        if is_hard_system_error(e):
            raise
        print(
            "[common] WARN next_stage probe failed: %r" % (e,),
            file=sys.stderr,
        )
        return False


def ok(
    pause=0.5,
    threshold=0.85,
    required=False,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    """Primary OK button (screens/Common/OK.png). Default optional.

    **Prefer not to call this in new macros** (see
    ``docs/dismiss-popup-scenarios.md`` §1.5). Result/claim/promo OK is owned
    by ``dismiss_popups`` / ``try_dismiss_popup`` on the *next* real control.
    This helper uses ``dismiss_popups=False`` (raw tap) and races that stack
    if both run on the same flow. Kept for legacy scripts + S6 guard.

    **S6 guard:** if **Next Stage** is visible (dual-button result panel, e.g.
    Sanctum multi-stage), press **Next Stage** instead of OK. On that dialog
    OK aborts the run; Next Stage continues. Returns True if Next Stage was
    clicked; does **not** fall through to OK after a successful Next Stage.
    If Next Stage was visible but the click failed, returns False and still
    does not press OK (avoids abort). Only when Next Stage is absent is OK
    pressed as usual.
    """
    # Prefer next_stage when co-visible — never abort multi-stage with OK.
    # Uses next_stage() helper (dismiss_popups=False), not try_dismiss_popup.
    # Inverse: try_dismiss_popup never calls ok() (see module INVARIANT).
    probe_thr = min(float(threshold), float(_DISMISS_THRESHOLD))
    if _next_stage_visible(threshold=probe_thr):
        print(
            "[common] ok() guard: NextStage visible — dismiss via next_stage "
            "(not OK; dual-button panel / Sanctum)",
            file=sys.stderr,
        )
        clicked = next_stage(
            pause=pause,
            threshold=threshold,
            required=False,
            wait=wait,
            times=1,
            which=which,
            confirm=None,
            confirm_pause=confirm_pause,
            confirm_timeout=confirm_timeout,
            confirm_retries=confirm_retries,
        )
        if clicked:
            print(
                "[common] ok() guard: next_stage clicked — skip OK",
                file=sys.stderr,
            )
            return True
        print(
            "[common] ok() guard: NextStage was visible but click failed — "
            "skip OK to avoid aborting multi-stage",
            file=sys.stderr,
        )
        if required:
            raise RuntimeError(
                "common.ok(): NextStage was visible (dual-button panel) but "
                "next_stage click failed; refusing OK to avoid abort"
            )
        return False

    return _tap(
        IMG_OK,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=False,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )


def close(
    pause=0.5,
    threshold=None,
    required=False,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    """Small close / X (screens/Common/Close-small.png). Default optional.

    **Prefer not to call this in new macros** (see
    ``docs/dismiss-popup-scenarios.md`` §1.5). Panel Close after a chest/fight
    is the same dual-owner problem as ``ok()``: this path is
    ``dismiss_popups=False`` while the next real control already walks Close
    via ``try_dismiss_popup``. Prefer chest/Collect/Back only; let dismiss
    clear leftover Close. Kept for legacy scripts.

    Default threshold matches auto-dismiss (``_DISMISS_THRESHOLD`` / 0.80);
    Close-small often scores ~0.83–0.87 on this game window scale.
    """
    if threshold is None:
        threshold = _DISMISS_THRESHOLD
    return _tap(
        IMG_CLOSE,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=False,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )


def battle(
    pause=0.8,
    threshold=0.85,
    required=True,
    wait=None,
    times=1,
    which="best",
    dismiss_retry_delay=None,
    confirm=None,
    confirm_isCommon=False,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    """Click Battle.png.

    Page-local confirm by default. For Common HUD::

        common.battle(confirm=\"IdleBattle.png\", confirm_isCommon=True)
    # or bare IdleBattle.png — Common is last-resort if not on the page
    """
    return _tap(
        IMG_BATTLE,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        dismiss_retry_delay=dismiss_retry_delay,
        confirm=confirm,
        confirm_isCommon=confirm_isCommon,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )


def idle_battle(
    pause=1.2,
    threshold=0.85,
    required=True,
    wait=None,
    times=1,
    which="best",
    dismiss_retry_delay=None,
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    return _tap(
        IMG_IDLE_BATTLE,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        dismiss_retry_delay=dismiss_retry_delay,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )

def next_stage(
    pause=0.2,
    threshold=0.85,
    required=False,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    """Next-stage / continue on result screens (screens/Common/NextStage.png)."""
    return _tap(
        IMG_NEXT_STAGE,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=False,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )

def skip_all(
    pause=0.3,
    threshold=0.85,
    required=True,
    wait=20,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    """Click SkipAll during battle.

    Defaults poll aggressively: short ``pause`` then long ``wait`` so the
    button is searched *while* the battle runs. A long pre-find ``pause``
    (old default 4s) often slept through the only Skip window after
    dismiss-path slowdowns on the previous step.
    """
    return _tap(
        IMG_SKIP_ALL,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )

def thumb_up(
    pause=0.8,
    threshold=0.85,
    required=False,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    return _tap(
        IMG_THUMB_UP,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )

def collect(
    pause=1.2,
    threshold=0.85,
    required=True,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    return _tap(
        IMG_COLLECT,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )

def free(
    pause=1.2,
    threshold=0.85,
    required=True,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    return _tap(
        IMG_FREE,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )

def exclaim(
    pause=1.2,
    threshold=0.85,
    required=True,
    wait=None,
    times=1,
    which="best",
    confirm=None,
    confirm_pause=None,
    confirm_timeout=None,
    confirm_retries=None,
):
    return _tap(
        IMG_EXCLAIM,
        pause=pause,
        threshold=threshold,
        required=required,
        wait=wait,
        times=times,
        which=which,
        dismiss_popups=True,
        confirm=confirm,
        confirm_pause=confirm_pause,
        confirm_timeout=confirm_timeout,
        confirm_retries=confirm_retries,
    )


def _dismiss_template(name, threshold=None, pause=0.15):
    """NewTemplate for a DISMISS_ORDER entry (isCommon, no nested dismiss).

    Used only by the auto-dismiss stack walk / probes — not by ``ok()`` etc.
    Always ``dismiss_popups=False`` so dismiss never re-enters ``try_dismiss_popup``.
    """
    filename = _DISMISS_IMAGES.get(name)
    if not filename:
        return None
    if threshold is None:
        threshold = _DISMISS_THRESHOLD
    return NewTemplate(
        filename,
        threshold=threshold,
        pause=pause,
        wait=None,
        isCommon=True,
        required=False,
        dismiss_popups=False,
    )


def _dismiss_touch(tpl):
    """Click a dismiss control via raw NewTemplate — never via ok()/close()/next_stage().

    Keeps auto-dismiss independent of the S6 ``ok()`` guard and avoids helper
    recursion into ``try_dismiss_popup``.
    """
    return tpl.touch(
        times=1,
        required=False,
        wait=None,
        dismiss_popups=False,
    )


def is_dismiss_image_name(name):
    """True if *name* is a dismiss-control basename (OK / Close / NextStage)."""
    if not name:
        return False
    base = os.path.basename(str(name).replace("\\", "/"))
    return base in _DISMISS_BASENAMES


def _find_pos_quiet(tpl):
    """Match center (x, y) or None; suppress NewTemplate not-found noise."""
    prev = getattr(tpl, "_qb_quiet_find", False)
    tpl._qb_quiet_find = True
    try:
        return tpl.find(wait=None, do_pause=False, dismiss_popups=False, required=False)
    finally:
        tpl._qb_quiet_find = prev


def _dismiss_probe_order(names=None):
    """Resolve probe order: always follow DISMISS_ORDER priority.

    When *names* is a subset (e.g. OVERLAY_DISMISS_NAMES), keep DISMISS_ORDER
    ranking so dual victory panels prefer next_stage over ok. A bare frozenset
    iteration order is not reliable.
    """
    if names is None:
        return DISMISS_ORDER
    name_set = frozenset(names)
    ordered = tuple(n for n in DISMISS_ORDER if n in name_set)
    extras = tuple(n for n in names if n not in name_set)
    return ordered + extras


def popup_blocking(threshold=None, for_target=None, names=None):
    """Return first matching dismiss control name on screen, else None.

    names : sequence or None
        Subset of ``DISMISS_ORDER`` to check (default: full list).
        Use ``OVERLAY_DISMISS_NAMES`` (next_stage/ok only) when the target
        is already visible — Close co-visible with Collect is not an overlay.
        Probe order always follows ``DISMISS_ORDER`` (next_stage before ok).

    for_target : optional label (logs only).
    """
    if threshold is None:
        threshold = _DISMISS_THRESHOLD
    thr = float(threshold)
    tgt = for_target or "?"
    order = _dismiss_probe_order(names)
    for name in order:
        if name not in _DISMISS_IMAGES:
            continue
        tpl = _dismiss_template(name, threshold=thr, pause=0.0)
        if tpl is None:
            continue
        try:
            xy = _find_pos_quiet(tpl)
        except Exception as e:
            if is_hard_system_error(e):
                raise
            print(
                "[common] WARN popup_blocking %s find failed: %r" % (name, e),
                file=sys.stderr,
            )
            continue
        if xy is not None:
            print(
                "[common] popup_blocking for_target=%s via %s pos=(%s,%s)"
                % (tgt, name, xy[0], xy[1]),
                file=sys.stderr,
            )
            return name
    return None


def overlay_blocking(threshold=None, for_target=None):
    """True overlay on top of a still-visible target: next_stage or ok only.

    Does **not** treat Close as an overlay (Collect+Close dialogs). Use this
    when the target template already matches (possibly dimmed under a modal).
    Probe order follows DISMISS_ORDER (next_stage preferred over ok).
    """
    return popup_blocking(
        threshold=threshold,
        for_target=for_target,
        names=OVERLAY_DISMISS_NAMES,
    )


def _pos_moved(before, after, min_dist=None):
    """True if match centers differ by at least min_dist pixels."""
    if before is None or after is None:
        return False
    if min_dist is None:
        min_dist = _DISMISS_MOVED_PX
    dx = float(after[0]) - float(before[0])
    dy = float(after[1]) - float(before[1])
    return (dx * dx + dy * dy) >= (float(min_dist) * float(min_dist))


def _wait_until_gone_or_moved(tpl, before_xy, timeout=None, poll=None):
    """Return ``\"gone\"``, ``\"moved\"``, or None if still at same place after timeout.

    Both stacked-popup layouts are valid after a front-layer click:
    - different position → match center jumps (``moved``)
    - same position → template still matches there (``None`` here; caller re-taps)
    """
    if timeout is None:
        timeout = _DISMISS_GONE_TIMEOUT_S
    if poll is None:
        poll = _DISMISS_GONE_POLL_S
    deadline = time.time() + float(timeout)
    while True:
        after_xy = _find_pos_quiet(tpl)
        if after_xy is None:
            return "gone"
        if _pos_moved(before_xy, after_xy):
            return "moved"
        if time.time() >= deadline:
            return None
        time.sleep(float(poll))


def try_dismiss_popup(pause=0.15, threshold=None, for_target=None, quick=False):
    """Dismiss blocking popups (default: next_stage → ok → close).

    Walks ``DISMISS_ORDER`` each tap. Stacked dialogs that reuse the same
    button art are handled in both common layouts:

    1. **Same position** — after click the control is still matched there;
       re-tap (lag or another layer on the same spot) until gone or budget
       runs out.
    2. **Different position** — match center moves → front layer cleared;
       keep dismissing the new match.

    **Dual victory (OK + Next Stage):** always pick **next_stage only**.
    Never press OK while Next Stage is visible (OK aborts multi-stage).

    **Close over dimmed Next Stage (S10):** Next Stage panels do **not**
    carry a Close control (only OK). If Next Stage keeps matching at the
    same position after a few taps *and* Close is co-visible, the Close is
    a separate top popup — escalate to Close. Do **not** escalate to OK.

    **S6 OK fallthrough guard:** once Next Stage was seen this call, never
    pick OK later in the same call (even if NextStage scores miss for a
    frame). Before any lone OK pick, soft-reprobe Next Stage at a lower
    threshold. Prefer Close or give up over aborting multi-stage with OK.

    Up to ``_DISMISS_MAX_LAYERS`` taps per call. After a full **gone**, a
    settle wait + empty re-scans allow a delayed next popup (another
    victory/reward panel) to finish painting before the stack walk ends.

    quick : bool
        If True, use a short empty-rescan / post-gone budget. Use for
        post-click overlay **drain** so the next script step (e.g. SkipAll
        during battle) is not starved by multi-second stack settling.
        Full mode (default) keeps longer rescans for intentional stack walks
        (Realms multi-NextStage, etc.).

    Default match threshold is ``_DISMISS_THRESHOLD`` (0.83), looser than
    typical 0.85 script clicks — small Close art often scores ~0.83–0.87.

    Does not recurse (``dismiss_popups=False`` on dismiss templates).
    Returns True only if at least one layer was confirmed cleared (gone or
    moved). Pure same-pos taps that never clear return False so callers do
    not spin forever on a sticky false match.

    **Does not call** ``ok()`` / ``close()`` / ``next_stage()`` — only
    ``_dismiss_template`` + ``_dismiss_touch``. See module INVARIANT.

    for_target : optional label of the NewTemplate waiting behind the popup
    (for logs only).
    """
    if threshold is None:
        threshold = _DISMISS_THRESHOLD
    thr = float(threshold)
    tgt = for_target or "?"
    any_cleared = False
    budget = max(1, int(_DISMISS_MAX_LAYERS))
    tap_i = 0
    empty_rescans = 0
    # Consecutive same-pos next_stage outcomes that did not clear (S10).
    next_stage_sticky = 0
    sticky_limit = max(1, int(_DISMISS_NEXT_STAGE_STICKY_BEFORE_CLOSE))
    # S6: once Next Stage was on screen this call, never fall through to OK
    # on a later frame (template miss / paint gap on dual panels).
    saw_next_stage = False
    soft_ns_thr = min(float(thr), float(_DISMISS_NEXT_STAGE_SOFT_THR))
    if quick:
        max_empty = max(0, int(_DISMISS_EMPTY_RESCANS_QUICK))
        empty_sleep = float(_DISMISS_EMPTY_RESCAN_S_QUICK)
        post_gone = float(_DISMISS_POST_GONE_S_QUICK)
    else:
        max_empty = max(0, int(_DISMISS_EMPTY_RESCANS))
        empty_sleep = float(_DISMISS_EMPTY_RESCAN_S)
        post_gone = float(_DISMISS_POST_GONE_S)

    print(
        "[common] try_dismiss_popup for_target=%s thr=%.2f soft_ns=%.2f "
        "quick=%s order=%s"
        % (tgt, thr, soft_ns_thr, bool(quick), list(DISMISS_ORDER)),
        file=sys.stderr,
    )

    def _probe_named(name, thr_use):
        """Quiet find for one dismiss name; return (tpl, xy) or None."""
        tpl = _dismiss_template(name, threshold=float(thr_use), pause=pause)
        if tpl is None:
            return None
        try:
            xy = _find_pos_quiet(tpl)
        except Exception as e:
            if is_hard_system_error(e):
                print(
                    "[common] HARD STOP dismiss %s: %s: %s"
                    % (name, type(e).__name__, e),
                    file=sys.stderr,
                )
                raise
            print(
                "[common] WARN dismiss %s find failed: %r" % (name, e),
                file=sys.stderr,
            )
            return None
        if xy is None:
            return None
        return (tpl, xy)

    while budget > 0:
        # Probe *all* of DISMISS_ORDER each frame so we can see Close under
        # a sticky Next Stage (S10). Decision still prefers next_stage over
        # ok (R1); Close only after sticky next_stage fails.
        matches = {}  # name -> (tpl, before_xy)
        for name in DISMISS_ORDER:
            hit = _probe_named(name, thr)
            if hit is None:
                continue
            matches[name] = hit

        if "next_stage" in matches:
            saw_next_stage = True

        picked = None  # (name, tpl, before_xy)
        if "next_stage" in matches:
            also = [n for n in DISMISS_ORDER if n != "next_stage" and n in matches]
            if also:
                print(
                    "[common] dismiss multi visible %s — next_stage preferred "
                    "(never ok while Next Stage up) for_target=%s sticky=%s"
                    % (["next_stage"] + also, tgt, next_stage_sticky),
                    file=sys.stderr,
                )
            # S10: after a few same-pos next_stage taps that did not clear,
            # Close co-visible is a top-layer promo/X (Next Stage panels
            # do not have Close). Escalate to Close; still never OK.
            if (
                next_stage_sticky >= sticky_limit
                and "close" in matches
            ):
                print(
                    "[common] dismiss S10: next_stage sticky=%s (limit %s) "
                    "and close co-visible — pick close (dimmed Next Stage "
                    "under Close) for_target=%s"
                    % (next_stage_sticky, sticky_limit, tgt),
                    file=sys.stderr,
                )
                tpl_c, xy_c = matches["close"]
                picked = ("close", tpl_c, xy_c)
            else:
                tpl_n, xy_n = matches["next_stage"]
                picked = ("next_stage", tpl_n, xy_n)
        elif "ok" in matches:
            # S6 rescue: OK alone at normal thr — soft-reprobe Next Stage
            # before allowing OK (dual panel often scores NextStage lower).
            soft_ns = None
            if soft_ns_thr < thr:
                soft_ns = _probe_named("next_stage", soft_ns_thr)
            if soft_ns is not None:
                saw_next_stage = True
                print(
                    "[common] dismiss S6: ok visible but next_stage soft-hit "
                    "at thr=%.2f pos=(%s,%s) — next_stage only (never ok) "
                    "for_target=%s"
                    % (soft_ns_thr, soft_ns[1][0], soft_ns[1][1], tgt),
                    file=sys.stderr,
                )
                picked = ("next_stage", soft_ns[0], soft_ns[1])
            elif saw_next_stage:
                # Next Stage was on screen earlier this call; OK now would
                # abort multi-stage if this is still a dual panel with a
                # template miss. Prefer Close, else rescan / stop — never OK.
                if "close" in matches:
                    print(
                        "[common] dismiss S6: refuse ok (saw next_stage this "
                        "call); pick close instead for_target=%s"
                        % (tgt,),
                        file=sys.stderr,
                    )
                    tpl_c, xy_c = matches["close"]
                    picked = ("close", tpl_c, xy_c)
                else:
                    print(
                        "[common] dismiss S6: refuse ok (saw next_stage this "
                        "call; no close) — skip ok for_target=%s"
                        % (tgt,),
                        file=sys.stderr,
                    )
                    # Fall through to empty-rescan / give-up without picking ok.
                    picked = None
                    if any_cleared and empty_rescans < max_empty:
                        empty_rescans += 1
                        print(
                            "[common] dismiss none yet; rescan %s/%s after clear "
                            "(S6 ok suppressed; stacked transition) for_target=%s"
                            % (empty_rescans, max_empty, tgt),
                            file=sys.stderr,
                        )
                        time.sleep(empty_sleep)
                        continue
                    print(
                        "[common] dismiss stop: would pick ok but next_stage "
                        "was seen this call (R1/S6) for_target=%s"
                        % (tgt,),
                        file=sys.stderr,
                    )
                    break
            else:
                also = [n for n in DISMISS_ORDER if n != "ok" and n in matches]
                if also:
                    print(
                        "[common] dismiss multi visible %s — prefer ok (order) "
                        "for_target=%s"
                        % (["ok"] + also, tgt),
                        file=sys.stderr,
                    )
                tpl_o, xy_o = matches["ok"]
                picked = ("ok", tpl_o, xy_o)
        elif "close" in matches:
            tpl_c, xy_c = matches["close"]
            picked = ("close", tpl_c, xy_c)

        if picked is None:
            # After clearing a layer, the next stacked panel may still be
            # animating in — re-scan a few times before giving up.
            if any_cleared and empty_rescans < max_empty:
                empty_rescans += 1
                print(
                    "[common] dismiss none yet; rescan %s/%s after clear "
                    "(stacked transition) for_target=%s"
                    % (empty_rescans, max_empty, tgt),
                    file=sys.stderr,
                )
                time.sleep(empty_sleep)
                continue
            print(
                "[common] dismiss none found for_target=%s thr=%.2f "
                "(next_stage/ok/close below threshold or absent)"
                % (tgt, thr),
                file=sys.stderr,
            )
            break

        empty_rescans = 0
        name, tpl, before_xy = picked
        tap_i += 1
        budget -= 1
        print(
            "[common] dismiss pick %s at pos=(%s,%s) for_target=%s tap=%s img=%s"
            % (name, before_xy[0], before_xy[1], tgt, tap_i, tpl._name()),
            file=sys.stderr,
        )
        # Raw template touch only — never common.ok() / next_stage() / close().
        try:
            clicked = _dismiss_touch(tpl)
        except Exception as e:
            if is_hard_system_error(e):
                print(
                    "[common] HARD STOP dismiss %s: %s: %s"
                    % (name, type(e).__name__, e),
                    file=sys.stderr,
                )
                raise
            print(
                "[common] WARN dismiss %s touch failed: %r" % (name, e),
                file=sys.stderr,
            )
            break

        if not clicked:
            print(
                "[common] dismiss %s touch returned False; stop for_target=%s"
                % (name, tgt),
                file=sys.stderr,
            )
            break

        if _DISMISS_GONE_PAUSE_S > 0:
            time.sleep(_DISMISS_GONE_PAUSE_S)

        outcome = _wait_until_gone_or_moved(tpl, before_xy)
        if outcome == "gone":
            print(
                "[common] dismissed popup via %s (gone after click, tap %s) "
                "for_target=%s"
                % (name, tap_i, tgt),
                file=sys.stderr,
            )
            any_cleared = True
            next_stage_sticky = 0
            if post_gone > 0:
                time.sleep(post_gone)
            continue

        if outcome == "moved":
            print(
                "[common] dismissed popup via %s "
                "(stack layer; different pos, tap %s) for_target=%s"
                % (name, tap_i, tgt),
                file=sys.stderr,
            )
            any_cleared = True
            next_stage_sticky = 0
            continue

        # Same position still visible: stacked same-pos layer and/or lag.
        # Re-tap on the next loop iteration (do not treat as failure yet).
        # Count next_stage stickiness for S10 Close escalation.
        if name == "next_stage":
            next_stage_sticky += 1
        elif name == "close":
            # Close also stuck — do not leave sticky high forever.
            next_stage_sticky = 0
        print(
            "[common] dismiss %s still at same pos after click "
            "(same-pos stack or lag; re-tap, budget left %s, tap %s, "
            "next_stage_sticky=%s) for_target=%s"
            % (name, budget, tap_i, next_stage_sticky, tgt),
            file=sys.stderr,
        )

    if not any_cleared and tap_i > 0:
        print(
            "[common] dismiss taps=%s but never gone/moved; giving up for_target=%s"
            % (tap_i, tgt),
            file=sys.stderr,
        )

    return any_cleared



