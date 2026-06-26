"""Code hash + environment fingerprinting for exec records.

Used by run_python / run_r when stamping a new execution_records row. Kept
in core/exec/ (not core/graph/) because:
  - `package_versions_for_session()` needs a live kernel session to query
    `pip freeze` / `installed.packages()`.
  - The cache is per-(thread, language); core/graph/* deliberately knows
    nothing about kernels.

`code_hash`: sha256 of UTF-8 code bytes. No normalization in v1 — we hash
exactly-what-ran. If false-drift becomes a problem (e.g. a stray trailing
newline flipping the hash), normalize then.

`env_fingerprint`: sha256 over a stable serialization of
(language_version, package_versions). Buckets installs that "look the
same" so the reproduce-from-exec UX can show "env unchanged" vs
"env drifted since this exec".

`package_versions`: cached on the kernel session object as
`_aba_pkg_versions` and `_aba_pkg_versions_at`. Invalidated by
`r_install` / `pip install` (Stage 1 does NOT yet wire those — first
post-install call refreshes naturally via the TTL).
"""
from __future__ import annotations
import hashlib
import json
import logging
import time

_log = logging.getLogger(__name__)

# How long a cached package-versions snapshot is reused before we
# re-snapshot. Long enough that normal back-to-back tool calls reuse it
# (≪ 100 ms refresh cost amortizes), short enough that a manual install
# becomes visible without explicit invalidation.
_PKG_VERSIONS_TTL_S = 600.0


def code_hash(code: str | bytes) -> str:
    """sha256:<hex> of the code. Stable across processes."""
    if isinstance(code, str):
        code = code.encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(code).hexdigest()


def env_fingerprint(language_version: str, package_versions: dict) -> str:
    """sha256:<hex> over a stable serialization of (lang ver, pkg versions).

    Keys are sorted so identical install sets always hash the same. Empty
    package_versions is fine — yields a fingerprint based on lang version
    alone, which is the right behaviour when the snapshot failed.
    """
    blob = json.dumps(
        {"language_version": language_version,
         "package_versions": package_versions or {}},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


# ── Per-session package-version capture ────────────────────────────────────

_PY_PROBE = r"""
def __aba_pkg_probe():
    import json, sys
    out = {"__lang_version__": sys.version.split()[0]}
    try:
        from importlib.metadata import distributions
        for dist in distributions():
            try:
                name = (dist.metadata["Name"] or "").lower()
                ver = dist.version or ""
                if name:
                    out[name] = ver
            except Exception:
                continue
    except Exception as e:
        out["__error__"] = repr(e)
    print("__ABA_PKG_BEGIN__"); print(json.dumps(out)); print("__ABA_PKG_END__")
__aba_pkg_probe(); del __aba_pkg_probe
"""

_R_PROBE = r"""
local({
  out <- list()
  out[["__lang_version__"]] <- paste(R.Version()$major, R.Version()$minor, sep=".")
  ip <- tryCatch(installed.packages(fields=NULL),
                 error=function(e) matrix(character(0), nrow=0, ncol=0))
  if (nrow(ip) > 0) {
    for (i in seq_len(nrow(ip))) {
      nm <- ip[i, "Package"]
      ver <- ip[i, "Version"]
      if (!is.na(nm) && nzchar(nm)) out[[nm]] <- ver
    }
  }
  cat("__ABA_PKG_BEGIN__\n")
  cat(jsonlite::toJSON(out, auto_unbox=TRUE), "\n", sep="")
  cat("__ABA_PKG_END__\n")
})
"""


def package_versions_for_session(sess, lang: str,
                                  *, force_refresh: bool = False) -> dict:
    """Return {package_name: version, "__lang_version__": "..."} for a
    persistent kernel session. Cached on the session for `_PKG_VERSIONS_TTL_S`
    so back-to-back calls don't re-probe.

    On probe failure returns an empty dict — the caller (exec_records.create)
    treats that as "no env captured" rather than failing the run.
    """
    now = time.time()
    cached = getattr(sess, "_aba_pkg_versions", None)
    cached_at = getattr(sess, "_aba_pkg_versions_at", 0.0)
    if (not force_refresh) and cached is not None and (now - cached_at) < _PKG_VERSIONS_TTL_S:
        return cached
    probe = _R_PROBE if lang == "r" else _PY_PROBE
    try:
        res = sess.execute(probe, timeout_s=30)
        text = (res.stdout or "") + "\n" + (res.stderr or "")
        if "__ABA_PKG_BEGIN__" not in text:
            return {}
        chunk = text.split("__ABA_PKG_BEGIN__", 1)[1].split("__ABA_PKG_END__", 1)[0]
        # R's jsonlite emits a single line; the python probe also single-line via json.dumps.
        line = chunk.strip().splitlines()
        body = line[0] if line else "{}"
        data = json.loads(body)
        if isinstance(data, dict):
            sess._aba_pkg_versions = data
            sess._aba_pkg_versions_at = now
            return data
        return {}
    except Exception as e:  # noqa: BLE001 — never fail the exec on a probe miss
        _log.warning("package_versions_for_session(%s) probe failed: %s", lang, e)
        return {}


def package_versions_for_interpreter(interp: str, lang: str,
                                     *, r_preamble: str = "", timeout_s: int = 30) -> dict:
    """Snapshot {package: version, "__lang_version__": …} by running the probe
    through an interpreter PATH (subprocess) — the background/Slurm analog of
    package_versions_for_session, which needs a live kernel. ``interp`` is the
    python the run used (the venv/overlay or an isolated env's python) or an
    Rscript. For R, ``r_preamble`` (the run's `.libPaths(...)` lines) makes the
    snapshot reflect the SAME libraries the run saw (e.g. an isolated R env).
    Returns {} on any failure (never blocks the run / the exec record)."""
    import subprocess
    try:
        if lang == "r":
            probe = (r_preamble + "\n" + _R_PROBE) if r_preamble else _R_PROBE
            cmd = [interp, "--vanilla", "-e", probe]
        else:
            cmd = [interp, "-c", _PY_PROBE]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        text = (res.stdout or "") + "\n" + (res.stderr or "")
        if "__ABA_PKG_BEGIN__" not in text:
            return {}
        chunk = text.split("__ABA_PKG_BEGIN__", 1)[1].split("__ABA_PKG_END__", 1)[0]
        line = chunk.strip().splitlines()
        data = json.loads(line[0]) if line else {}
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        _log.warning("package_versions_for_interpreter(%s) failed: %s", lang, e)
        return {}


def invalidate_package_versions(sess) -> None:
    """Called by r_install / pip-install paths so the NEXT package_versions
    capture re-probes immediately rather than waiting on the TTL.
    """
    if hasattr(sess, "_aba_pkg_versions"):
        del sess._aba_pkg_versions
    if hasattr(sess, "_aba_pkg_versions_at"):
        del sess._aba_pkg_versions_at
