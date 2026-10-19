# -*- coding: utf-8 -*-
"""Chain fine-grained .air scripts with per-step error policy.

Each step runs as a **subprocess** (same process model as CLI / AirtestIDE).
On failure the orchestrator applies the step's ``on_error`` policy:

  retry     — re-run the whole step up to ``max_retries`` extra times
  continue  — record failure and proceed to the next step
  abort     — stop the chain immediately

Load chains from JSON (or build ``Chain`` / ``Step`` in Python). See
``tools/run_chain.py`` and ``AirtestScripts/chains/``.
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import OrderedDict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# AirtestScripts/ (parent of _lib/)
SCRIPTS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Repo root (parent of AirtestScripts/)
REPO_ROOT = os.path.dirname(SCRIPTS_ROOT)

ON_ERROR_RETRY = "retry"
ON_ERROR_CONTINUE = "continue"
ON_ERROR_ABORT = "abort"
ON_ERROR_VALUES = frozenset({ON_ERROR_RETRY, ON_ERROR_CONTINUE, ON_ERROR_ABORT})

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ABORTED = "aborted"


def _norm_on_error(value, default=ON_ERROR_ABORT):
    if value is None:
        return default
    s = str(value).strip().lower()
    if s not in ON_ERROR_VALUES:
        raise ValueError(
            "on_error must be one of %s, got %r"
            % (sorted(ON_ERROR_VALUES), value)
        )
    return s


def _as_int(value, default, name="value"):
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be int, got %r" % (name, value))
    if n < 0:
        raise ValueError("%s must be >= 0, got %s" % (name, n))
    return n


def _as_float(value, default, name="value"):
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be float, got %r" % (name, value))


def resolve_under_roots(path, roots=None):
    """Resolve path if absolute or relative to any of *roots* (first hit wins)."""
    if path is None:
        raise ValueError("path is required")
    p = os.path.expanduser(str(path))
    if os.path.isabs(p):
        return os.path.normpath(p)
    roots = roots or (SCRIPTS_ROOT, REPO_ROOT, os.getcwd())
    for root in roots:
        cand = os.path.normpath(os.path.join(root, p))
        if os.path.exists(cand):
            return cand
    # Default: relative to AirtestScripts even if not present yet
    return os.path.normpath(os.path.join(SCRIPTS_ROOT, p))


def resolve_air_script(script_ref):
    """
    Map a step reference to the ``.py`` entrypoint.

    Accepts:
      - path to ``Name.air`` folder
      - path to ``Name.air/Name.py``
      - path to a ``.py`` file
      - relative path under AirtestScripts/ or repo root
    """
    raw = resolve_under_roots(script_ref)
    if os.path.isfile(raw) and raw.lower().endswith(".py"):
        return raw
    if os.path.isdir(raw):
        base = os.path.basename(raw)
        if base.lower().endswith(".air"):
            name = base[:-4]  # strip .air
            py = os.path.join(raw, name + ".py")
            if os.path.isfile(py):
                return py
            # fallback: single .py inside folder
            pys = [
                f
                for f in os.listdir(raw)
                if f.lower().endswith(".py") and not f.startswith("_")
            ]
            if len(pys) == 1:
                return os.path.join(raw, pys[0])
            raise FileNotFoundError(
                "No %s.py in %s (found %s)" % (name, raw, pys)
            )
        raise FileNotFoundError("Not a .air folder or .py file: %s" % raw)
    # Maybe user passed Name.air without folder existing
    if raw.lower().endswith(".air") and not os.path.exists(raw):
        raise FileNotFoundError("Missing .air folder: %s" % raw)
    raise FileNotFoundError("Cannot resolve script: %s (tried %s)" % (script_ref, raw))


def default_python():
    """Prefer qb_env when present; else current interpreter."""
    for rel in (
        os.path.join("qb_env", "Scripts", "python.exe"),
        os.path.join("qb_env", "bin", "python"),
    ):
        cand = os.path.join(REPO_ROOT, rel)
        if os.path.isfile(cand):
            return cand
    return sys.executable


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


class Step(object):
    """One .air (or .py) unit in a chain."""

    def __init__(
        self,
        script,
        name=None,
        on_error=None,
        max_retries=None,
        retry_delay=None,
        timeout=None,
        enabled=True,
        env=None,
        python=None,
        args=None,
    ):
        self.script = script
        self.name = name or _step_display_name(script)
        self.on_error = on_error  # None → chain defaults
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.enabled = bool(enabled) if enabled is not None else True
        self.env = dict(env) if env else None
        self.python = python
        self.args = list(args) if args else []

    def resolved_policy(self, defaults):
        """Merge step overrides with chain defaults → plain dict."""
        d = defaults or {}
        return {
            "on_error": _norm_on_error(
                self.on_error if self.on_error is not None else d.get("on_error"),
                default=ON_ERROR_ABORT,
            ),
            "max_retries": _as_int(
                self.max_retries
                if self.max_retries is not None
                else d.get("max_retries"),
                0,
                "max_retries",
            ),
            "retry_delay": _as_float(
                self.retry_delay
                if self.retry_delay is not None
                else d.get("retry_delay"),
                1.0,
                "retry_delay",
            ),
            "timeout": (
                None
                if (
                    self.timeout
                    if self.timeout is not None
                    else d.get("timeout")
                )
                is None
                else _as_float(
                    self.timeout
                    if self.timeout is not None
                    else d.get("timeout"),
                    None,
                    "timeout",
                )
            ),
            "python": self.python or d.get("python") or default_python(),
        }

    def to_dict(self):
        out = OrderedDict()
        out["script"] = self.script
        out["name"] = self.name
        if self.on_error is not None:
            out["on_error"] = self.on_error
        if self.max_retries is not None:
            out["max_retries"] = self.max_retries
        if self.retry_delay is not None:
            out["retry_delay"] = self.retry_delay
        if self.timeout is not None:
            out["timeout"] = self.timeout
        if not self.enabled:
            out["enabled"] = False
        if self.env:
            out["env"] = self.env
        if self.python:
            out["python"] = self.python
        if self.args:
            out["args"] = self.args
        return out

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, str):
            return cls(script=data)
        if not isinstance(data, dict):
            raise TypeError("step must be str or dict, got %r" % (type(data),))
        if "script" not in data and "air" not in data and "path" not in data:
            raise ValueError("step dict needs 'script' (or 'air'/'path'): %r" % data)
        script = data.get("script") or data.get("air") or data.get("path")
        return cls(
            script=script,
            name=data.get("name"),
            on_error=data.get("on_error"),
            max_retries=data.get("max_retries"),
            retry_delay=data.get("retry_delay"),
            timeout=data.get("timeout"),
            enabled=data.get("enabled", True),
            env=data.get("env"),
            python=data.get("python"),
            args=data.get("args"),
        )


def _step_display_name(script):
    s = str(script).replace("\\", "/").rstrip("/")
    base = os.path.basename(s)
    if base.lower().endswith(".air"):
        return base[:-4]
    if base.lower().endswith(".py"):
        return base[:-3]
    return base or s


class Chain(object):
    """Named ordered list of steps + default error policy."""

    def __init__(self, name="chain", steps=None, defaults=None, description=None):
        self.name = name or "chain"
        self.description = description or ""
        self.defaults = dict(defaults or {})
        # normalize defaults keys if present
        if "on_error" in self.defaults:
            self.defaults["on_error"] = _norm_on_error(self.defaults["on_error"])
        self.steps = []
        for s in steps or []:
            if isinstance(s, Step):
                self.steps.append(s)
            else:
                self.steps.append(Step.from_dict(s))

    def to_dict(self):
        out = OrderedDict()
        out["name"] = self.name
        if self.description:
            out["description"] = self.description
        if self.defaults:
            out["defaults"] = self.defaults
        out["steps"] = [s.to_dict() for s in self.steps]
        return out

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise TypeError("chain must be a dict")
        return cls(
            name=data.get("name") or "chain",
            description=data.get("description"),
            defaults=data.get("defaults") or data.get("default") or {},
            steps=data.get("steps") or [],
        )

    def with_steps(self, steps):
        """Return a new Chain with the same name/defaults but *steps*."""
        return Chain(
            name=self.name,
            description=self.description,
            defaults=dict(self.defaults),
            steps=list(steps),
        )


def _normalize_air_needle(ref):
    """Normalize a user ref like Quest-Arena-battle.air → quest-arena-battle."""
    s = str(ref or "").strip().replace("\\", "/")
    if not s:
        return ""
    base = os.path.basename(s.rstrip("/"))
    lower = base.lower()
    if lower.endswith(".air"):
        base = base[:-4]
    elif lower.endswith(".py"):
        base = base[:-3]
    return base.lower()


def step_matches_air(step, ref):
    """True if *step* is the given .air project / step name / script path.

    Accepts e.g. ``Quest-Arena-battle``, ``Quest-Arena-battle.air``,
    ``screens/Quest/Arena/Quest-Arena-battle.air``, or a chain step *name*
    that contains that project basename.
    """
    if step is None or not ref:
        return False
    needle = _normalize_air_needle(ref)
    if not needle:
        return False
    name = (step.name or "").lower()
    script = (step.script or "").replace("\\", "/").lower()
    script_base = _normalize_air_needle(script)
    # Exact basename match preferred
    if script_base == needle:
        return True
    if name == needle:
        return True
    # Path ends with /Name.air or /Name.air/Name.py
    if script.rstrip("/").endswith("/" + needle + ".air"):
        return True
    if script.endswith("/" + needle + ".py"):
        return True
    if script.endswith(needle + ".air/" + needle + ".py"):
        return True
    # Step display name often "1.5 Arena battle" — match .air basename inside
    # script path only for substring to avoid accidental "Quest" matching all.
    if needle in script_base:
        return script_base == needle or script_base.startswith(needle + "-")
    # Explicit: needle appears as full path segment
    if ("/" + needle + ".air") in script or script.endswith(needle + ".air"):
        return True
    # Chain step name contains project id (e.g. name lists the .air)
    if needle in name.replace(" ", "").replace("_", "-"):
        # weak: only if name also has no other .air-like token conflict
        return needle in name or needle.replace("-", "") in name.replace(" ", "").replace("-", "")
    return False


def find_step_index(chain, start_from, from_end=False):
    """Index of first (or last) step matching *start_from*, or -1."""
    if not isinstance(chain, Chain):
        if isinstance(chain, dict):
            chain = Chain.from_dict(chain)
        else:
            raise TypeError("chain must be Chain or dict")
    indices = []
    for i, step in enumerate(chain.steps):
        if step_matches_air(step, start_from):
            indices.append(i)
    if not indices:
        return -1
    return indices[-1] if from_end else indices[0]


def slice_chain_from(chain, start_from, inclusive=True):
    """
    Return a new Chain starting at the step matching *start_from* (.air name).

    Raises ValueError if no step matches.
    """
    if not isinstance(chain, Chain):
        if isinstance(chain, dict):
            chain = Chain.from_dict(chain)
        else:
            raise TypeError("chain must be Chain or dict")
    idx = find_step_index(chain, start_from)
    if idx < 0:
        names = [("%s (%s)" % (s.name, s.script)) for s in chain.steps]
        raise ValueError(
            "No chain step matches start_from=%r. Steps:\n  %s"
            % (start_from, "\n  ".join(names))
        )
    if not inclusive:
        idx += 1
    if idx >= len(chain.steps):
        raise ValueError(
            "start_from=%r with inclusive=False leaves no steps" % (start_from,)
        )
    return chain.with_steps(chain.steps[idx:])


def filter_chain_steps(chain, only_steps=None):
    """Keep steps whose name or script contains any of *only_steps* needles."""
    if not isinstance(chain, Chain):
        if isinstance(chain, dict):
            chain = Chain.from_dict(chain)
        else:
            raise TypeError("chain must be Chain or dict")
    if not only_steps:
        return chain
    needles = [s.lower() for s in only_steps]
    kept = []
    for step in chain.steps:
        name = (step.name or "").lower()
        script = (step.script or "").lower()
        if any(n in name or n in script for n in needles):
            kept.append(step)
    return chain.with_steps(kept)


# Fallback only: when no per-step log_file was written, embed short excerpts.
# Full NewTemplate/common streams always live in step log files when step_log_dir is set.
_REPORT_FALLBACK_STDOUT_TAIL = 500
_REPORT_FALLBACK_STDERR_TAIL = 2000


class AttemptResult(object):
    def __init__(
        self,
        attempt,
        ok,
        returncode=None,
        duration_s=0.0,
        error=None,
        stdout="",
        stderr="",
        timed_out=False,
        script_py=None,
        cmd=None,
        log_file=None,
    ):
        self.attempt = attempt
        self.ok = bool(ok)
        self.returncode = returncode
        self.duration_s = duration_s
        self.error = error
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.timed_out = bool(timed_out)
        self.script_py = script_py
        self.cmd = cmd or []
        self.log_file = log_file

    def to_dict(self):
        # Keep the JSON report compact: outcome metadata + pointer to log_file.
        # Do not re-embed stdout/stderr when a full step log was written (that
        # duplication is what bloated chain reports).
        d = {
            "attempt": self.attempt,
            "ok": self.ok,
            "returncode": self.returncode,
            "duration_s": round(float(self.duration_s), 3),
            "error": self.error,
            "timed_out": self.timed_out,
            "script_py": self.script_py,
            "log_file": self.log_file,
        }
        if not self.log_file:
            out_full = self.stdout or ""
            err_full = self.stderr or ""
            if out_full:
                d["stdout_tail"] = _tail(out_full, _REPORT_FALLBACK_STDOUT_TAIL)
            if err_full:
                d["stderr_tail"] = _tail(err_full, _REPORT_FALLBACK_STDERR_TAIL)
        return d


class StepResult(object):
    def __init__(self, step, policy, status, attempts=None, decided=None):
        self.step = step
        self.policy = policy
        self.status = status  # ok | failed | skipped | aborted (last outcome)
        self.attempts = list(attempts or [])
        self.decided = decided  # retry exhausted → continue|abort, etc.

    @property
    def ok(self):
        return self.status == STATUS_OK

    @property
    def last_attempt(self):
        return self.attempts[-1] if self.attempts else None

    def to_dict(self):
        return {
            "name": self.step.name,
            "script": self.step.script,
            "status": self.status,
            "decided": self.decided,
            "policy": self.policy,
            "attempts": [a.to_dict() for a in self.attempts],
        }


class ChainResult(object):
    def __init__(self, chain, step_results=None, aborted=False, abort_reason=None):
        self.chain = chain
        self.step_results = list(step_results or [])
        self.aborted = bool(aborted)
        self.abort_reason = abort_reason
        self.started_at = ""  # type: str
        self.finished_at = ""  # type: str

    @property
    def ok(self):
        if self.aborted:
            return False
        return all(r.status in (STATUS_OK, STATUS_SKIPPED) for r in self.step_results)

    @property
    def failed_steps(self):
        return [r for r in self.step_results if r.status == STATUS_FAILED]

    def exit_code(self):
        """0 success, 1 aborted or failed-with-continue, 2 config/resolve error."""
        if self.aborted:
            return 1
        if any(r.status == STATUS_FAILED for r in self.step_results):
            return 1
        return 0

    def to_dict(self):
        log_files = []
        for r in self.step_results:
            for a in r.attempts:
                if getattr(a, "log_file", None):
                    log_files.append(a.log_file)
        return {
            "name": self.chain.name,
            "ok": self.ok,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "exit_code": self.exit_code(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "step_log_files": log_files,
            "note": (
                "Child NewTemplate/common logs are on stderr. "
                "Full streams are in each attempt's log_file (and step_log_files); "
                "this JSON is outcome-only (no stdout/stderr dumps)."
            ),
            "steps": [r.to_dict() for r in self.step_results],
        }

    def summary_lines(self):
        lines = []
        lines.append(
            "=== chain %r ok=%s aborted=%s ==="
            % (self.chain.name, self.ok, self.aborted)
        )
        if self.abort_reason:
            lines.append("abort_reason: %s" % self.abort_reason)
        for r in self.step_results:
            last = r.last_attempt
            detail = ""
            if last is not None:
                detail = " rc=%s t=%.1fs" % (
                    last.returncode,
                    last.duration_s,
                )
                if last.error:
                    detail += " err=%s" % (last.error[:120],)
            lines.append(
                "  [%s] %s policy=%s%s"
                % (r.status, r.step.name, r.policy.get("on_error"), detail)
            )
        return lines


def _tail(text, n):
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    if len(text) <= n:
        return text
    return (
        "…[truncated %s chars; open log_file for full stream]…\n" % (len(text) - n,)
        + text[-n:]
    )


def _safe_filename(name):
    """Filesystem-safe short name for step log files."""
    s = str(name or "step")
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        elif ch in (" ", "/", "\\", ":", "|"):
            out.append("_")
        else:
            out.append("_")
    cleaned = "".join(out).strip("._") or "step"
    return cleaned[:80]


def _step_log_path(log_dir, step_name, attempt_no):
    """Return path for a step attempt log (creates log_dir)."""
    if not log_dir:
        return None
    try:
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
    except OSError as e:
        print(
            "[orchestrator] WARN cannot create step log dir %s: %r" % (log_dir, e),
            file=sys.stderr,
        )
        return None
    base = "%s_attempt%s.log" % (_safe_filename(step_name), int(attempt_no))
    path = os.path.join(log_dir, base)
    if os.path.isfile(path):
        path = os.path.join(
            log_dir,
            "%s_%s.log" % (os.path.splitext(base)[0], time.strftime("%H%M%S")),
        )
    return path


def write_attempt_log(log_dir, step_name, attempt_no, result, path=None):
    """Write full stdout+stderr for one attempt; set result.log_file. Return path.

    If *path* already has a live-streamed log, append a footer instead of
    overwriting the body.
    """
    if path is None:
        path = _step_log_path(log_dir, step_name, attempt_no)
    if not path:
        return None
    try:
        # If live tee already wrote the body, only append footer metadata.
        already = os.path.isfile(path) and os.path.getsize(path) > 0
        mode = "a" if already else "w"
        with open(path, mode, encoding="utf-8", errors="replace") as f:
            if already:
                f.write("\n===== END attempt ok=%s returncode=%s duration_s=%.3f error=%s =====\n"
                        % (result.ok, result.returncode, result.duration_s, result.error))
            else:
                f.write("# step=%s attempt=%s ok=%s returncode=%s\n" % (
                    step_name,
                    attempt_no,
                    result.ok,
                    result.returncode,
                ))
                f.write("# script_py=%s\n" % (result.script_py,))
                f.write("# cmd=%s\n" % (" ".join(result.cmd or []),))
                f.write("# duration_s=%.3f error=%s\n" % (result.duration_s, result.error))
                f.write("\n===== STDOUT =====\n")
                f.write(result.stdout or "")
                f.write("\n===== STDERR =====\n")
                f.write(result.stderr or "")
                f.write("\n")
        result.log_file = path
        return path
    except OSError as e:
        print(
            "[orchestrator] WARN cannot write step log %s: %r" % (path, e),
            file=sys.stderr,
        )
        return None


def _pipe_reader(stream, chunks, echo_file, log_file_handle, log_lock, prefix=""):
    """Read *stream* line-by-line; append to chunks; echo + flush log live."""
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            chunks.append(line)
            try:
                if echo_file is not None:
                    echo_file.write(prefix + line)
                    echo_file.flush()
            except Exception:
                pass
            if log_file_handle is not None:
                try:
                    with log_lock:
                        log_file_handle.write(line)
                        log_file_handle.flush()
                except Exception:
                    pass
    except Exception as e:
        chunks.append("\n[orchestrator] pipe reader error: %r\n" % (e,))
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _communicate_live(proc, timeout=None, log_path=None, echo=True):
    """
    Read child stdout/stderr as they arrive (line-buffered from child side
    may still batch; we flush every line we get).

    Unlike ``proc.communicate()``, this:
      - can stream to the console while the step runs
      - can append to *log_path* incrementally (survives Ctrl+C mid-step)
    """
    out_chunks = []
    err_chunks = []
    log_fh = None
    log_lock = threading.Lock()
    if log_path:
        try:
            log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
            log_fh.write(
                "# live step log started %s pid=%s\n"
                % (time.strftime("%Y-%m-%dT%H:%M:%S"), proc.pid)
            )
            log_fh.write(
                "===== STDOUT / STDERR (as produced; interleaved by arrival) =====\n"
            )
            log_fh.flush()
        except OSError as e:
            print(
                "[orchestrator] WARN open live log %s: %r" % (log_path, e),
                file=sys.stderr,
            )
            log_fh = None

    echo_out = sys.stdout if echo else None
    echo_err = sys.stderr if echo else None

    threads = []
    if proc.stdout is not None:
        t = threading.Thread(
            target=_pipe_reader,
            args=(proc.stdout, out_chunks, echo_out, log_fh, log_lock, ""),
            name="orch-stdout",
        )
        t.daemon = True
        t.start()
        threads.append(t)
    if proc.stderr is not None:
        t = threading.Thread(
            target=_pipe_reader,
            args=(proc.stderr, err_chunks, echo_err, log_fh, log_lock, ""),
            name="orch-stderr",
        )
        t.daemon = True
        t.start()
        threads.append(t)

    timed_out = False
    try:
        if timeout is not None:
            proc.wait(timeout=timeout)
        else:
            proc.wait()
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    except KeyboardInterrupt:
        # Leave partial log on disk; re-raise after cleanup join.
        try:
            proc.kill()
        except Exception:
            pass
        for t in threads:
            t.join(timeout=2)
        if log_fh is not None:
            try:
                log_fh.write("\n# interrupted by KeyboardInterrupt\n")
                log_fh.flush()
                log_fh.close()
            except Exception:
                pass
        raise
    for t in threads:
        t.join(timeout=30)
    if log_fh is not None:
        try:
            log_fh.write(
                "\n# process ended returncode=%s timed_out=%s\n"
                % (proc.returncode, timed_out)
            )
            log_fh.flush()
            log_fh.close()
        except Exception:
            pass

    return (
        "".join(out_chunks),
        "".join(err_chunks),
        timed_out,
        log_path if log_fh is not None or (log_path and os.path.isfile(log_path)) else None,
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_chain_json(path):
    path = resolve_under_roots(
        path, roots=(SCRIPTS_ROOT, REPO_ROOT, os.getcwd(), os.path.join(SCRIPTS_ROOT, "chains"))
    )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    chain = Chain.from_dict(data)
    return chain, path


def load_chain(path):
    """Load chain from .json (or .py defining CHAIN / get_chain())."""
    path = resolve_under_roots(
        path,
        roots=(
            SCRIPTS_ROOT,
            REPO_ROOT,
            os.getcwd(),
            os.path.join(SCRIPTS_ROOT, "chains"),
        ),
    )
    if not os.path.isfile(path):
        raise FileNotFoundError("Chain file not found: %s" % path)
    lower = path.lower()
    if lower.endswith(".json"):
        return load_chain_json(path)[0]
    if lower.endswith(".py"):
        # Execute in isolated dict
        ns = {"__file__": path, "__name__": "__chain__"}
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        exec(compile(code, path, "exec"), ns, ns)
        if "get_chain" in ns and callable(ns["get_chain"]):
            chain = ns["get_chain"]()
        elif "CHAIN" in ns:
            chain = ns["CHAIN"]
        else:
            raise ValueError(
                "Python chain %s must define CHAIN or get_chain()" % path
            )
        if isinstance(chain, dict):
            chain = Chain.from_dict(chain)
        if not isinstance(chain, Chain):
            raise TypeError("get_chain()/CHAIN must be Chain or dict")
        return chain
    raise ValueError("Unsupported chain format (use .json or .py): %s" % path)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_step_once(
    step,
    policy,
    python=None,
    extra_env=None,
    capture_output=True,
    log=None,
    step_log_dir=None,
    attempt_no=1,
):
    """
    Run one attempt of *step* as a subprocess.

    Returns AttemptResult. Does not apply retry/continue/abort.

    Child ``NewTemplate`` / ``common`` logs go to **stderr**. With
    ``capture_output=True`` they are collected here (and optionally written
    under ``step_log_dir``). With ``capture_output=False`` they stream to the
    console and are **not** stored in the result.
    """
    log = log or (lambda msg: print(msg, file=sys.stderr))
    py = python or policy.get("python") or default_python()
    t0 = time.time()
    try:
        script_py = resolve_air_script(step.script)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        log("[orchestrator] resolve failed %s: %s" % (step.name, err))
        ar = AttemptResult(
            attempt=attempt_no,
            ok=False,
            returncode=-1,
            duration_s=time.time() - t0,
            error=err,
            stdout="",
            stderr=err,
            timed_out=False,
            script_py=None,
            cmd=[py, str(step.script)],
        )
        write_attempt_log(step_log_dir, step.name, attempt_no, ar)
        return ar

    # -u: unbuffered stdio so [NewTemplate]/[common] lines stream live
    cmd = [py, "-u", script_py] + list(step.args or [])
    env = os.environ.copy()
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    if step.env:
        env.update({str(k): str(v) for k, v in step.env.items()})
    # Ensure AirtestScripts on PYTHONPATH for any side imports
    pp = env.get("PYTHONPATH", "")
    parts = [SCRIPTS_ROOT] + ([pp] if pp else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONUNBUFFERED"] = "1"

    cwd = os.path.dirname(script_py)
    timeout = policy.get("timeout")
    log(
        "[orchestrator] run %s attempt  (cwd=%s)\n  cmd: %s"
        % (step.name, cwd, " ".join(cmd))
    )
    timed_out = False
    error = None
    stdout = ""
    stderr = ""
    returncode = None
    live_log_path = None
    # Named Popen args (not **kwargs dict) so type checkers see PIPE/int correctly.
    out_pipe = subprocess.PIPE if capture_output else None
    err_pipe = subprocess.PIPE if capture_output else None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=out_pipe,
            stderr=err_pipe,
            universal_newlines=True,
            bufsize=1,
        )
        if capture_output:
            live_log_path = _step_log_path(step_log_dir, step.name, attempt_no)
            if live_log_path:
                log("[orchestrator] live log → %s" % live_log_path)
            stdout, stderr, timed_out, live_log_path = _communicate_live(
                proc,
                timeout=timeout,
                log_path=live_log_path,
                echo=True,
            )
            returncode = proc.returncode if proc.returncode is not None else (
                -9 if timed_out else -1
            )
            if timed_out and error is None:
                error = "timeout after %ss" % timeout
        else:
            # Inherit console; no capture.
            if timeout is not None:
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    error = "timeout after %ss" % timeout
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    proc.wait(timeout=5)
            else:
                proc.wait()
            returncode = proc.returncode
    except KeyboardInterrupt:
        duration = time.time() - t0
        ar = AttemptResult(
            attempt=attempt_no,
            ok=False,
            returncode=-2,
            duration_s=duration,
            error="KeyboardInterrupt",
            stdout=stdout or "",
            stderr=(stderr or "") + "\nKeyboardInterrupt\n",
            timed_out=False,
            script_py=script_py,
            cmd=cmd,
            log_file=live_log_path,
        )
        if live_log_path:
            write_attempt_log(
                step_log_dir, step.name, attempt_no, ar, path=live_log_path
            )
        raise
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, e)
        returncode = -1
        stderr = (stderr or "") + "\n" + traceback.format_exc()

    duration = time.time() - t0
    ok = (returncode == 0) and not timed_out and error is None
    if not ok and error is None:
        error = "exit code %s" % returncode
    # Prefer last non-empty stderr line as short error
    if not ok and stderr:
        lines = [ln.strip() for ln in stderr.replace("\r\n", "\n").split("\n") if ln.strip()]
        # look for RuntimeError / Traceback tail
        for ln in reversed(lines):
            if "Error" in ln or "Exception" in ln or "Traceback" in ln:
                error = ln[:300]
                break
        if error == "exit code %s" % returncode and lines:
            error = lines[-1][:300]

    ar = AttemptResult(
        attempt=attempt_no,
        ok=ok,
        returncode=returncode,
        duration_s=duration,
        error=error,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        script_py=script_py,
        cmd=cmd,
        log_file=live_log_path,
    )
    if step_log_dir and capture_output:
        path = write_attempt_log(
            step_log_dir, step.name, attempt_no, ar, path=live_log_path
        )
        if path:
            log(
                "[orchestrator] step log %s (%s stderr chars) → %s"
                % (step.name, len(ar.stderr or ""), path)
            )
    elif capture_output and (stdout or stderr):
        log(
            "[orchestrator] captured %s stdout=%s stderr=%s chars "
            "(no step_log_dir — short tails only if report has no log_file)"
            % (step.name, len(stdout or ""), len(stderr or ""))
        )
    return ar


def run_step(
    step,
    defaults=None,
    extra_env=None,
    capture_output=True,
    log=None,
    step_log_dir=None,
):
    """
    Run *step* with its policy (retry loop). Returns StepResult.

    Decision after exhaustion of retries is encoded in ``decided`` and
    ``status``:
      - ok → status ok
      - on_error continue → status failed, decided continue
      - on_error abort → status aborted, decided abort
      - on_error retry with retries left → re-run (internal)
    """
    log = log or (lambda msg: print(msg, file=sys.stderr))
    policy = step.resolved_policy(defaults)
    if not step.enabled:
        log("[orchestrator] skip disabled step %s" % step.name)
        return StepResult(step, policy, STATUS_SKIPPED, attempts=[], decided="skip")

    attempts = []
    max_retries = policy["max_retries"]
    on_error = policy["on_error"]
    # abort = fail-fast (no extra attempts). retry/continue honor max_retries,
    # then abort or continue respectively.
    if on_error == ON_ERROR_ABORT:
        total_tries = 1
    else:
        total_tries = 1 + max_retries

    for i in range(total_tries):
        attempt_no = i + 1
        if i > 0:
            delay = policy["retry_delay"]
            log(
                "[orchestrator] retry %s (%s/%s) after %.1fs"
                % (step.name, attempt_no, total_tries, delay)
            )
            if delay and delay > 0:
                time.sleep(delay)

        ar = run_step_once(
            step,
            policy,
            python=policy.get("python"),
            extra_env=extra_env,
            capture_output=capture_output,
            log=log,
            step_log_dir=step_log_dir,
            attempt_no=attempt_no,
        )
        attempts.append(ar)

        if ar.ok:
            log(
                "[orchestrator] OK %s (attempt %s, %.1fs)"
                % (step.name, attempt_no, ar.duration_s)
            )
            return StepResult(step, policy, STATUS_OK, attempts=attempts, decided="ok")

        log(
            "[orchestrator] FAIL %s attempt %s/%s: %s"
            % (step.name, attempt_no, total_tries, ar.error)
        )

        if i < total_tries - 1:
            continue
        break

    # All attempts failed — decide chain control
    if on_error == ON_ERROR_CONTINUE:
        log("[orchestrator] continue after failure: %s" % step.name)
        return StepResult(
            step, policy, STATUS_FAILED, attempts=attempts, decided=ON_ERROR_CONTINUE
        )
    # retry exhausted or abort
    if on_error == ON_ERROR_RETRY and max_retries > 0:
        log("[orchestrator] retries exhausted for %s → abort" % step.name)
    else:
        log("[orchestrator] abort on failure: %s" % step.name)
    return StepResult(
        step, policy, STATUS_ABORTED, attempts=attempts, decided=ON_ERROR_ABORT
    )


def run_chain(
    chain,
    extra_env=None,
    capture_output=True,
    log=None,
    stop_on_abort=True,
    step_log_dir=None,
    start_from=None,
    only_steps=None,
):
    """
    Execute all steps in *chain*. Returns ChainResult.

    When a step decides ``abort``, remaining steps are not run (if
    stop_on_abort). Failed+continue steps leave status failed but the chain
    proceeds; overall ChainResult.ok is False if any step failed or aborted.

    step_log_dir : str or None
        If set (and capture_output), write **full** child stdout/stderr per
        attempt under this directory. JSON report points at those files and
        does not re-embed stream dumps.
    start_from : str or None
        Skip steps before the first match for this .air project / step name
        (e.g. ``Quest-Arena-battle`` or ``Quest-Arena-battle.air``). That step
        is included. Raises ValueError if nothing matches.
    only_steps : list of str or None
        If set, keep only steps whose name/script contains any needle
        (applied after *start_from*).
    """
    if not isinstance(chain, Chain):
        if isinstance(chain, dict):
            chain = Chain.from_dict(chain)
        else:
            raise TypeError("chain must be Chain or dict")

    log = log or (lambda msg: print(msg, file=sys.stderr))

    if start_from:
        chain = slice_chain_from(chain, start_from, inclusive=True)
        log(
            "[orchestrator] start_from=%r → %s steps beginning at %r"
            % (start_from, len(chain.steps), chain.steps[0].name if chain.steps else None)
        )
    if only_steps:
        chain = filter_chain_steps(chain, only_steps)
        log(
            "[orchestrator] only_steps=%r → %s steps"
            % (only_steps, len(chain.steps))
        )
        if not chain.steps:
            raise ValueError("only_steps=%r matched no steps" % (only_steps,))

    result = ChainResult(chain)
    result.started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    if step_log_dir:
        try:
            if not os.path.isdir(step_log_dir):
                os.makedirs(step_log_dir)
            log("[orchestrator] step logs → %s" % step_log_dir)
        except OSError as e:
            log("[orchestrator] WARN step_log_dir %s: %r" % (step_log_dir, e))
            step_log_dir = None
    log(
        "[orchestrator] start chain %r (%s steps) defaults=%s capture=%s"
        % (chain.name, len(chain.steps), chain.defaults, capture_output)
    )

    for step in chain.steps:
        sr = run_step(
            step,
            defaults=chain.defaults,
            extra_env=extra_env,
            capture_output=capture_output,
            log=log,
            step_log_dir=step_log_dir,
        )
        result.step_results.append(sr)
        if sr.status == STATUS_ABORTED and stop_on_abort:
            result.aborted = True
            last = sr.last_attempt
            result.abort_reason = "%s: %s" % (
                step.name,
                (last.error if last else "aborted"),
            )
            log("[orchestrator] chain aborted at %s" % step.name)
            break

    result.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    for line in result.summary_lines():
        log(line)
    return result


def run_chain_file(path, **kwargs):
    """Load chain from path and run it."""
    chain = load_chain(path)
    return run_chain(chain, **kwargs)
