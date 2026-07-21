"""Environment-selection campaign guards.

Fix order (each guard proven failing on the pre-fix code):
  1. env_layers() honesty — the R session layer must appear on the
     rlib-overlay topology. Pre-fix, an undefined name inside the branch
     raised NameError, the blanket except swallowed it, and the layer was
     silently dropped exactly where the layering question matters.
  2. set_active() validation — binding an env into a mismatched language
     slot (or a nonexistent env) is refused loudly; the setter used to
     write blindly.
"""
from __future__ import annotations

import os

import pytest

from core.compute import named_envs
from core.compute.errors import ComputeError
from core.exec import env_integrity

PID = "prj_envres"


# ── 1. env_layers() on the overlay topology ──────────────────────────────────

def _layers_with(monkeypatch, project_id, *, py_runtime, r_runtime,
                 py_prefix=None, r_prefix=None):
    """env_layers() against a faked session runtime; returns (out, ensure_calls)
    so tests can assert the branch under test actually ran (armed)."""
    from core.compute import base_env, project_env
    calls: list[str] = []

    def _ensure(pid, lang):
        calls.append(lang)
        if lang == "r":
            return {"runtime": dict(r_runtime), "prefix": r_prefix,
                    "materialized": True}
        return {"runtime": dict(py_runtime), "prefix": py_prefix,
                "materialized": True}

    monkeypatch.setattr(base_env, "active", lambda lang: True)
    monkeypatch.setattr(project_env, "ensure", _ensure)
    return env_integrity.env_layers(project_id), calls


def test_r_session_layer_survives_overlay_topology(monkeypatch, tmp_path):
    """The flagship guard: on the rlib-overlay topology (session R library
    riding a read-only base) the R session layer must be reported — path,
    mode, and the overlay's actual package content."""
    rlib = tmp_path / "rlib"
    (rlib / "overlaypkg").mkdir(parents=True)
    (rlib / "overlaypkg" / "DESCRIPTION").write_text("Package: overlaypkg\n")
    out, calls = _layers_with(monkeypatch, "prjL",
                              py_runtime={}, r_runtime={"rlib": str(rlib)})
    assert "r" in calls  # armed: the R session branch was actually reached
    sess = [l for l in out["r"]["layers"] if l["tier"] == "session"]
    assert len(sess) == 1, (
        "R session layer silently dropped on the rlib-overlay topology "
        f"(r layers: {out['r']['layers']})")
    assert sess[0]["mode"] == "rlib-overlay"
    assert sess[0]["path"] == str(rlib)
    assert [p["name"] for p in sess[0]["packages"]] == ["overlaypkg"]


def test_r_activation_only_yields_no_session_layer(monkeypatch, tmp_path):
    """Absent shape: no rlib, no direct prefix → no session layer, no crash."""
    out, calls = _layers_with(monkeypatch, "prjL", py_runtime={}, r_runtime={})
    assert "r" in calls
    assert [l for l in out["r"]["layers"] if l["tier"] == "session"] == []


def test_r_direct_exec_branch_unaffected(monkeypatch, tmp_path):
    """The other topology: a direct-exec R session reports its library path."""
    prefix = tmp_path / "renv"
    lib = prefix / "lib" / "R" / "library"
    lib.mkdir(parents=True)
    monkeypatch.setattr(
        env_integrity, "_r_packages_by_lib",
        lambda libs, rscript=None: {
            os.path.realpath(str(lib)): [{"name": "p", "version": "1"}]})
    out, _ = _layers_with(monkeypatch, "prjL", py_runtime={},
                          r_runtime={"direct_exec": True}, r_prefix=prefix)
    sess = [l for l in out["r"]["layers"] if l["tier"] == "session"]
    assert len(sess) == 1
    assert sess[0]["path"] == str(lib)
    assert sess[0]["packages"] == [{"name": "p", "version": "1"}]


def test_python_pylib_overlay_parity(monkeypatch, tmp_path):
    """The Python analog of the overlay branch keeps working (regression)."""
    pylib = tmp_path / "pylib"
    pylib.mkdir()
    out, _ = _layers_with(monkeypatch, "prjL",
                          py_runtime={"pylib": str(pylib)}, r_runtime={})
    sess = [l for l in out["python"]["layers"] if l["tier"] == "session"]
    assert len(sess) == 1
    assert sess[0]["path"] == str(pylib)


# ── 2. set_active() validation ───────────────────────────────────────────────

@pytest.fixture
def registry(monkeypatch, tmp_path):
    from core import config as _cfg
    monkeypatch.setattr(_cfg, "PROJECTS_DIR", tmp_path / "projects")
    named_envs._save(PID, {
        "envs": {
            "renv": {"env_id": "e:1", "language": "r", "packages": []},
            "pyenv": {"env_id": "e:2", "language": "python", "packages": []},
            "legacy": {"env_id": "e:3"},  # pre-language row → python
        },
        "active": {}, "default": {}})
    return PID


def test_set_active_rejects_language_mismatch(registry):
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "renv", "python")
    assert ei.value.code == "env.language_mismatch"
    assert named_envs.get_active(registry, "python") == "default"  # unchanged


def test_set_active_rejects_mismatch_via_default_lang(registry):
    """Absent shape: no lang argument defaults the slot — still validated."""
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "renv")
    assert ei.value.code == "env.language_mismatch"


def test_set_active_accepts_matching_language(registry):
    named_envs.set_active(registry, "renv", "r")
    assert named_envs.get_active(registry, "r") == "renv"
    named_envs.set_active(registry, "pyenv", "python")
    assert named_envs.get_active(registry, "python") == "pyenv"


def test_set_active_rejects_unknown_env(registry):
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "ghost", "python")
    assert ei.value.code == "unknown_env"
    assert named_envs.get_active(registry, "python") == "default"


def test_set_active_reserved_name_resets_pointer(registry):
    """'default' (and friends) are the reset path — no row exists, no
    validation applies, the pointer returns to the served stack."""
    named_envs.set_active(registry, "renv", "r")
    named_envs.set_active(registry, "default", "r")
    assert named_envs.get_active(registry, "r") == "default"


def test_set_active_legacy_row_language_defaults_python(registry):
    """A pre-language registry row counts as python: binds into the python
    slot, refuses the r slot."""
    named_envs.set_active(registry, "legacy", "python")
    assert named_envs.get_active(registry, "python") == "legacy"
    with pytest.raises(ComputeError) as ei:
        named_envs.set_active(registry, "legacy", "r")
    assert ei.value.code == "env.language_mismatch"


# ── 3. resolve_env() — the ONE selection policy ──────────────────────────────

def test_resolve_env_explicit_wins_over_pointer(registry):
    named_envs.set_active(registry, "pyenv", "python")
    assert named_envs.resolve_env(registry, "python", explicit="other") == "other"


def test_resolve_env_explicit_reserved_or_empty_means_default(registry):
    named_envs.set_active(registry, "pyenv", "python")
    for explicit in ("", "default", "BASE", "shared", "  "):
        assert named_envs.resolve_env(registry, "python", explicit=explicit) is None


def test_resolve_env_pointer_when_no_explicit(registry):
    named_envs.set_active(registry, "pyenv", "python")
    assert named_envs.resolve_env(registry, "python") == "pyenv"


def test_resolve_env_default_when_pointer_unset(registry):
    assert named_envs.resolve_env(registry, "python") is None
    assert named_envs.resolve_env("", "python") is None      # no project


def test_resolve_env_cross_language_isolation(registry):
    """A python pointer must never leak into the r lane, and vice versa."""
    named_envs.set_active(registry, "pyenv", "python")
    assert named_envs.resolve_env(registry, "r") is None
    named_envs.set_active(registry, "renv", "r")
    assert named_envs.resolve_env(registry, "r") == "renv"
    assert named_envs.resolve_env(registry, "python") == "pyenv"


def test_resolve_env_dangling_pointer_falls_back(registry, capsys):
    """A pointer naming a vanished env (corruption/manual edit) falls back to
    the default session WITH a warning — never a crash on a bare run."""
    named_envs._save(PID, {"envs": {}, "active": {"python": "gone"},
                           "default": {}})
    assert named_envs.resolve_env(registry, "python") is None
    assert "gone" in capsys.readouterr().out    # armed: the warning printed


def test_resolve_env_explicit_is_stripped(registry):
    assert named_envs.resolve_env(registry, "python", explicit=" x ") == "x"


# ── 4. census: private re-derivations are forbidden ──────────────────────────

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
_GET_ACTIVE_RX = r"\bget_active\s*\("
_PENV_RX = (r"\b(?:project_env|_penv)\s*\.\s*(?:ensure|runtime|exec_argv|"
            r"interpreter|prefix|install|run_installer|snapshot)\s*\(")

# Pointer READS live in the policy owner; everything else renders or guards.
_POINTER_READERS = {
    "core/compute/named_envs.py":
        "policy owner — get_active/resolve_env are defined here; forget() "
        "refuses to drop the active env",
    "core/exec/compute_env.py":
        "display — the per-turn context line renders both language pointers "
        "verbatim (no resolution decision is taken)",
    "content/bio/tools/discovery.py":
        "display + guard — env-overview active marks and evict/forget "
        "active-env refusal (no resolution decision is taken)",
}

# Default-session consumers: each entry is a deliberate decision, not an
# accident — a NEW file resolving the default session on behalf of running
# code must either take a resolved env from its caller or be argued in here.
_DEFAULT_SESSION_CONSUMERS = {
    "core/compute/project_env.py": "self — the default-session manager",
    "core/exec/env_integrity.py":
        "default arm after the resolve_env decision (package probe), plus "
        "multi-env reports (overview/layers enumerate every tier explicitly)",
    "core/exec/verify.py":
        "probe helpers — the argv builder is supplied by the caller's own "
        "resolution decision",
    "core/exec/kernels/weft.py":
        "inherit-plumbing — receives env_name resolved upstream; default arm "
        "+ the cross-language bridge pin (needs any real interpreter)",
    "core/exec/run.py":
        "one-shot runner — explicit env parameter from the caller; default arm",
    "core/jobs/weft_submitter.py":
        "job snapshotter — the env NAME was frozen at submit by the resolving "
        "lane; default-env jobs snapshot the default session at dispatch",
    "content/bio/tools/run_exec.py":
        "default arm — session pre-warm after resolve_env returned None",
    "content/bio/tools/discovery.py":
        "default arm of the capability installer + R session lanes after the "
        "pointer consult (_pointer_env)",
    "content/bio/lifecycle/revisions.py":
        "provenance env-diff vs the default session (KNOWN GAP: should become "
        "pointer-aware — docs/arch/envs known gaps)",
    "content/bio/viewers/launchers/pagoda3.py":
        "platform-owned converter — its own deps (lstar) live in the default "
        "session; running it in a promoted env would miss them (KNOWN GAP: "
        "R object deserialization may need the user's promoted packages — "
        "two-sided dependency)",
}


def _files_matching(root: Path, pattern: str) -> set:
    rx = re.compile(pattern)
    hits = set()
    for p in root.rglob("*.py"):
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        if rx.search(p.read_text(errors="replace")):
            hits.add(rel)
    return hits


def test_pointer_read_census():
    hits = _files_matching(_BACKEND, _GET_ACTIVE_RX)
    assert "core/compute/named_envs.py" in hits   # armed: scanner sees the owner
    offenders = hits - set(_POINTER_READERS)
    assert not offenders, (
        "private active-pointer reads outside the policy/display allowlist — "
        f"route through named_envs.resolve_env: {sorted(offenders)}")


def test_default_session_consumer_census():
    hits = _files_matching(_BACKEND, _PENV_RX)
    assert "core/compute/project_env.py" in hits  # armed: scanner sees self-calls
    offenders = hits - set(_DEFAULT_SESSION_CONSUMERS)
    assert not offenders, (
        "new default-session consumer(s) — resolve through named_envs."
        f"resolve_env or argue an allowlist entry: {sorted(offenders)}")


def test_census_scanner_catches_offenders(tmp_path):
    """PROVEN: the scanner flags both idioms on a synthetic offender tree —
    a regex that silently matches nothing would render both censuses green."""
    (tmp_path / "rogue.py").write_text(
        "from core.compute.named_envs import get_active\n"
        "e = get_active(pid, 'python')\n")
    (tmp_path / "sneak.py").write_text(
        "from core.compute import project_env\n"
        "argv = project_env.exec_argv(pid, 'python', ['-c', 'pass'])\n")
    assert _files_matching(tmp_path, _GET_ACTIVE_RX) == {"rogue.py"}
    assert _files_matching(tmp_path, _PENV_RX) == {"sneak.py"}


# ── 5. wiring: the lanes deliver what resolve_env decided ────────────────────

def test_pointer_env_helper_slot_rules(registry):
    from content.bio.tools.discovery import _pointer_env
    assert _pointer_env(PID, "python") is None            # nothing set
    named_envs.set_active(PID, "pyenv", "python")
    assert _pointer_env(PID, "python") == ("pyenv", "python")
    assert _pointer_env(PID, "r") is None                 # wrong-language slot
    assert _pointer_env(PID, None) == ("pyenv", "python")  # one slot decides
    named_envs.set_active(PID, "renv", "r")
    assert _pointer_env(PID, None) is None                # two slots: ambiguous
    assert _pointer_env(PID, "r") == ("renv", "r")


def test_bg_submit_kwargs_carries_pointer_env(registry):
    from content.bio.tools.run_exec import bg_submit_kwargs
    assert bg_submit_kwargs({}, PID)["env"] is None                 # unset
    named_envs.set_active(PID, "pyenv", "python")
    assert bg_submit_kwargs({}, PID)["env"] == "pyenv"              # pointer
    assert bg_submit_kwargs({"env": "other"}, PID)["env"] == "other"   # explicit
    assert bg_submit_kwargs({"env": "default"}, PID)["env"] is None    # reset


def test_remote_sync_python_submit_carries_pointer_env(registry, monkeypatch):
    from content.bio.tools import run_exec as rex
    from core.jobs import submit as _submit
    named_envs.set_active(PID, "pyenv", "python")
    captured = {}

    def _fake_submit(code, **kw):
        captured.update(kw)
        raise ValueError("halt-after-capture")
    monkeypatch.setattr(_submit, "submit_python_job", _fake_submit)
    out = rex._run_remote_sync({"site": "sX", "code": "1"}, {}, PID, "t1",
                               "run_python")
    assert out["status"] == "error" and "halt-after-capture" in out["note"]
    assert captured.get("env") == "pyenv"   # armed via the halt note above


def test_remote_sync_r_submit_carries_r_pointer(registry, monkeypatch):
    from content.bio.tools import run_exec as rex
    from core.jobs import submit as _submit
    named_envs.set_active(PID, "renv", "r")
    named_envs.set_active(PID, "pyenv", "python")   # must NOT leak into run_r
    captured = {}

    def _fake_submit(code, **kw):
        captured.update(kw)
        raise ValueError("halt-after-capture")
    monkeypatch.setattr(_submit, "submit_r_job", _fake_submit)
    out = rex._run_remote_sync({"site": "sX", "code": "1"}, {}, PID, "t1",
                               "run_r")
    assert out["status"] == "error" and "halt-after-capture" in out["note"]
    assert captured.get("env") == "renv"


def test_run_python_bare_call_lands_in_promoted_env(registry, monkeypatch):
    """Full run_python() entry: pointer set, no env argument → the named-env
    lane (kernels-off one-shot fallback) receives the promoted env."""
    from content.bio.tools import run_exec as rex
    from core import config as _cfg, projects
    named_envs.set_active(PID, "pyenv", "python")
    monkeypatch.setattr(projects, "current", lambda: PID)
    monkeypatch.setattr(_cfg, "KERNEL_ENABLED", False)
    monkeypatch.setattr(named_envs, "ensure_ready", lambda env_id, **k: None)
    captured = {}

    def _fake_named(env, code, lang, timeout_s):
        captured.update(env=env, lang=lang)
        return {"status": "ok", "execution_mode": "isolated"}
    monkeypatch.setattr(rex, "_run_in_named_env", _fake_named)
    out = rex.run_python({"code": "x=1"}, {"thread_id": "t1"})
    assert captured == {"env": "pyenv", "lang": "python"}
    assert out.get("execution_mode") == "isolated"


def test_run_r_bare_call_lands_in_promoted_env(registry, monkeypatch):
    """Full run_r() entry: the r pointer promotes an isolated R env for bare
    calls — the campaign's acceptance wiring (system-lib case)."""
    from content.bio.tools import run_exec as rex
    from core import projects
    from core.exec import compute_env as _cemod, router as _router
    named_envs.set_active(PID, "renv", "r")
    monkeypatch.setattr(projects, "current", lambda: PID)
    monkeypatch.setattr(_cemod, "compute_env", lambda: {"mode": "local"})

    class _Choice:
        location, rationale = "local", "test"
    monkeypatch.setattr(_router, "decide", lambda **k: _Choice())
    captured = {}

    def _fake_named(env, code, lang, timeout_s):
        captured.update(env=env, lang=lang)
        return {"status": "ok", "execution_mode": "isolated"}
    monkeypatch.setattr(rex, "_run_in_named_env", _fake_named)
    out = rex.run_r({"code": "1"}, {"thread_id": "t1"})
    assert captured == {"env": "renv", "lang": "r"}
    assert out.get("execution_mode") == "isolated"


def test_run_r_explicit_env_still_wins(registry, monkeypatch):
    from content.bio.tools import run_exec as rex
    from core import projects
    from core.exec import compute_env as _cemod, router as _router
    named_envs.set_active(PID, "renv", "r")
    monkeypatch.setattr(projects, "current", lambda: PID)
    monkeypatch.setattr(_cemod, "compute_env", lambda: {"mode": "local"})

    class _Choice:
        location, rationale = "local", "test"
    monkeypatch.setattr(_router, "decide", lambda **k: _Choice())
    captured = {}
    monkeypatch.setattr(
        rex, "_run_in_named_env",
        lambda env, code, lang, t: (captured.update(env=env)
                                    or {"status": "ok"}))
    rex.run_r({"code": "1", "env": "otherenv"}, {"thread_id": "t1"})
    assert captured == {"env": "otherenv"}


def test_ensure_capability_targets_promoted_env(registry, monkeypatch):
    """The self-consistent-lie fix: a bare capability request lands in — and
    is verified against — the promoted env, not the default session."""
    from content.bio.tools import discovery as disc
    from core import catalog as _cat, projects
    named_envs.set_active(PID, "pyenv", "python")
    monkeypatch.setattr(projects, "current", lambda: PID)
    monkeypatch.setattr(_cat, "resolve_capability", lambda name: None)
    captured = {}
    monkeypatch.setattr(
        disc, "_extend_into_named_env",
        lambda env, pkgs, cap: (captured.update(env=env, pkgs=list(pkgs))
                                or {"status": "ready", "env": env}))
    out = disc.ensure_capability({"name": "somepkg"}, {"thread_id": ""})
    assert captured == {"env": "pyenv", "pkgs": ["somepkg"]}
    assert out["status"] == "ready" and out["env"] == "pyenv"


def test_ensure_capability_explicit_env_beats_pointer(registry, monkeypatch):
    from content.bio.tools import discovery as disc
    from core import catalog as _cat, projects
    named_envs.set_active(PID, "pyenv", "python")
    monkeypatch.setattr(projects, "current", lambda: PID)
    monkeypatch.setattr(_cat, "resolve_capability", lambda name: None)
    captured = {}
    monkeypatch.setattr(
        disc, "_extend_into_named_env",
        lambda env, pkgs, cap: (captured.update(env=env)
                                or {"status": "ready", "env": env}))
    disc.ensure_capability({"name": "somepkg", "env": "renv"}, {"thread_id": ""})
    assert captured == {"env": "renv"}


def test_package_status_probes_promoted_env(registry, monkeypatch):
    from core.exec import env_integrity as ei
    named_envs.set_active(PID, "pyenv", "python")
    calls = {}

    def _fake_run_in(pid, env, code, timeout_s=120):
        calls.update(pid=pid, env=env)
        return {"ok": True,
                "stdout": 'ABA_JSON={"name": "x", "loads": true, "version": "9"}'}
    monkeypatch.setattr(named_envs, "run_in", _fake_run_in)
    out = ei.python_package_status("x", project_id=PID)
    assert calls == {"pid": PID, "env": "pyenv"}   # armed: probe ran THERE
    assert out["loads"] is True and out["version"] == "9"
    assert out["tier"] == "isolated" and out["env"] == "pyenv"


def test_package_status_default_arm_unchanged(registry):
    """No pointer → the classic probe path (runtime interpreter fallback)."""
    from core.exec import env_integrity as ei
    out = ei.python_package_status("json", project_id=PID)
    assert out["loads"] is True and out["tier"] in ("base", "session")


# ── 6. the set_active_env tool speaks both languages ─────────────────────────

def test_set_active_env_tool_promotes_r(registry, monkeypatch):
    from content.bio.tools import discovery as disc
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: PID)
    out = disc.set_active_env({"name": "renv", "language": "r"}, {})
    assert out["status"] == "ok"
    assert out["language"] == "r" and out["active_env"] == "renv"
    assert named_envs.get_active(PID, "r") == "renv"
    assert named_envs.get_active(PID, "python") == "default"  # untouched


def test_set_active_env_tool_surfaces_mismatch(registry, monkeypatch):
    from content.bio.tools import discovery as disc
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: PID)
    out = disc.set_active_env({"name": "renv"}, {})   # defaults python slot
    assert out["status"] == "error"
    assert "cannot be the active" in out["note"]
    assert named_envs.get_active(PID, "python") == "default"


def test_set_active_env_tool_rejects_bad_language(registry, monkeypatch):
    from content.bio.tools import discovery as disc
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: PID)
    out = disc.set_active_env({"name": "pyenv", "language": "julia"}, {})
    assert out["status"] == "error" and "language" in out["note"]


def test_set_active_env_tool_keeps_legacy_key(registry, monkeypatch):
    from content.bio.tools import discovery as disc
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: PID)
    out = disc.set_active_env({"name": "pyenv"}, {})
    assert out["active_python_env"] == "pyenv" == out["active_env"]
    out2 = disc.set_active_env({"name": "default"}, {})
    assert out2["active_python_env"] == "default"
