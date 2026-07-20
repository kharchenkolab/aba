"""Test helper — put ensure_capability / run_python / run_r into weft PACK MODE
without a real substrate.

The W3.5 cutover made a base pack MANDATORY (no served-base/micromamba fallback):
`ensure_capability` and the run lanes resolve a project session interpreter via
`base_env`/`project_env`. Unit tests that exercise capability routing or a run
shouldn't stand up a real weft session, so this presents a session interpreter
(the backend python, which can import stdlib for probes) + a no-op session install.
"""
from __future__ import annotations
import sys


def enable(monkeypatch, *, py: str | None = None, rscript: str | None = None):
    """Declare python/r base packs and stub the session so pack-mode lanes run
    against the backend interpreter. Returns a list that records session installs
    as (lang, eco, specs)."""
    py = py or sys.executable
    rscript = rscript or (str(_which("Rscript")) if _which("Rscript") else "/usr/bin/false")
    from content.bio.tools import discovery as _disc
    from core.compute import base_env as _be, project_env as _pe

    monkeypatch.setattr(_be, "pack_name",
                        lambda lang: {"python": "python-bio", "r": "r-bio"}.get(lang))
    # topology-blind probe seam: a BUILDER (args -> argv), mirroring
    # project_env.exec_argv's direct-exec shape over the stub interpreter
    monkeypatch.setattr(_disc, "_default_probe_argv",
                        lambda: (lambda args: [py, *[str(a) for a in args]]))

    class _P:  # a Path-like the run lanes join "/bin/python" onto is not needed —
        pass   # they call project_env.interpreter directly (mocked below).

    def _interp(pid, lang):
        from pathlib import Path
        return Path(rscript if lang == "r" else py)
    monkeypatch.setattr(_pe, "interpreter", _interp)

    installs: list = []

    def _install(pid, lang, specs, *, eco="pypi"):
        installs.append((lang, eco, list(specs)))
        return {"ok": True, "session_id": "ses_test"}
    monkeypatch.setattr(_pe, "install", _install)

    from pathlib import Path
    # The "session prefix" is the backend venv (has bin/python + ipykernel) for
    # python, or the Rscript's env prefix for R — so the kernel path
    # (_ensure_base_*_kernelspec → prefix/"bin"/…) resolves a real interpreter.
    _py_prefix = Path(sys.prefix)
    _r_prefix = Path(rscript).parent.parent

    def _prefix(pid, lang):
        return _r_prefix if lang == "r" else _py_prefix
    monkeypatch.setattr(_pe, "prefix", _prefix)

    # The runtime block (Step 2 contract): the stubbed session is a plain
    # on-disk prefix → direct exec. run.py's default lane and exec_argv/runtime
    # consume ensure()['runtime']; interpreter/prefix stay stubbed above.
    def _rt(pid, lang):
        return {"source": "session", "env_id": None,
                "prefix": str(_prefix(pid, lang)), "activation": "true",
                "ns_wrap": False, "direct_exec": True}
    monkeypatch.setattr(_pe, "ensure",
                        lambda pid, lang: {"session_id": "ses_test",
                                           "prefix": _prefix(pid, lang),
                                           "base_env_id": "env:test",
                                           "runtime": _rt(pid, lang),
                                           "materialized": True})
    return installs


def _which(name):
    import shutil
    from pathlib import Path
    p = shutil.which(name)
    return Path(p) if p else None
