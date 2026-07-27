"""No CONTROLLER path may appear in a REMOTE kernel's setup block.

This is a bug CLASS, not a bug. The setup code aba injects into a kernel is
assembled from several independent pieces, each of which decides on its own
whether to be remote-aware — so every new piece is a fresh chance to embed a
path that exists only on the controller. It has happened three times:

  * DATA_DIR / ARTIFACTS_DIR — got a `remote` branch (correct today).
  * RETICULATE_PYTHON — added later (2026-07-21) with no `remote` branch, so
    every REMOTE R kernel was pinned to `/Users/…/.aba/env/bin/python`, a path
    that cannot exist on a Linux node. Verified live on mendel.
  * `_ensure_kernel_cwd` — sent `setwd(<controller run dir>)` to remote kernels
    (a different injection point, same mistake).

So this guard checks the ASSEMBLED block rather than any one contributor: it
asserts no controller-only absolute path survives into the remote form. A new
setup contributor that forgets `remote` fails here without anyone remembering
to test it — which is the only way a class-level bug stays fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from core.exec.kernels.weft import _reticulate_pin_r, _weft_setup_code  # noqa: E402


def _controller_roots() -> list[str]:
    """Absolute prefixes that are meaningful ONLY on this controller."""
    import sys as _s
    from core.config import DATA_DIR, ARTIFACTS_DIR
    roots = {str(Path(_s.executable).parent.parent), str(DATA_DIR),
             str(ARTIFACTS_DIR), str(Path.home() / ".aba")}
    return [r for r in roots if r and r not in ("/", "")]


@pytest.mark.parametrize("lang", ["r", "python"])
def test_remote_setup_block_carries_no_controller_path(lang):
    """THE class guard, over the assembled block."""
    block = _weft_setup_code(lang, remote=True)
    for root in _controller_roots():
        assert root not in block, (
            f"remote {lang} setup embeds the controller path {root!r} — it does "
            f"not exist on the site. Offending block:\n{block}")


@pytest.mark.parametrize("lang", ["r", "python"])
def test_remote_setup_binds_dirs_to_the_kernel_sandbox(lang):
    """ARMED: prove the block is real and remote-shaped, so the purity test
    above cannot pass vacuously on an empty or degenerate block."""
    block = _weft_setup_code(lang, remote=True)
    assert len(block) > 40, block
    assert ("getwd()" in block) if lang == "r" else ("getcwd()" in block)
    assert "DATA_DIR" in block and "WORK_DIR" in block


def test_reticulate_pin_is_local_only():
    """The specific instance: a remote R kernel gets no pin at all, because
    every candidate interpreter is a controller path."""
    assert _reticulate_pin_r(remote=True) == ""
    local = _reticulate_pin_r(remote=False)
    # local may legitimately be empty (no interpreter resolvable), but when it
    # pins, it must pin something concrete
    if local:
        assert "RETICULATE_PYTHON" in local


def test_local_setup_still_uses_project_dirs():
    """CEILING: the local block must keep binding the project's real dirs —
    over-applying the remote form would strip a working feature."""
    block = _weft_setup_code("python", remote=False)
    from core.config import DATA_DIR
    assert str(DATA_DIR) in block or "DATA_DIR" in block
    assert "getcwd()" not in block.split("WORK_DIR")[0], \
        "local DATA_DIR must be the project dir, not the cwd"


# ── the same property, one layer out: DETACHED task payloads ────────────────
#
# A detached site shares no filesystem with the controller, so a controller path
# in its task is meaningless there. `_build_detached_task` says so in a comment
# ("NO controller paths, NO ABA_* env — the node shares nothing") — this makes
# it enforced instead of aspirational, because a comment is what the reticulate
# pin and the controller-setwd both walked past.
#
# The SHARED-FS lane is deliberately exempt: it bootstraps with `sys.executable`
# precisely because that absolute path IS valid on every node there. Conflating
# the two lanes would either break that lane or make this guard meaningless.

def _detached_task(tmp_path, monkeypatch, lang="python"):
    """Build a real detached task, capturing the payload the node would get."""
    from core.jobs import weft_submitter as ws
    captured: dict = {}

    class _Ad:
        def sync_call(self, verb, *a, **kw):
            assert verb == "data_register"
            payload_dir = Path(a[0])
            captured["files"] = {f.name: f.read_text(errors="replace")
                                 for f in payload_dir.iterdir() if f.is_file()}
            return {"ref": "ref-payload"}
    monkeypatch.setattr(ws, "_adapter", lambda: _Ad())
    monkeypatch.setattr(ws, "site_contract", lambda site: "detached")

    s = ws.WeftSubmitter.__new__(ws.WeftSubmitter)
    s.site = "siteA"
    monkeypatch.setattr(s, "_run_dir", lambda job: tmp_path, raising=False)
    monkeypatch.setattr(s, "_site_kind", lambda site: "ssh", raising=False)
    job = {"id": "job_1", "kind": "run_r" if lang == "r" else "run_python",
           "title": "t", "params": {"code": "print(1)", "project_id": "p1"}}
    task = s._build_detached_task(job, job["params"], env_id=None, site="siteA")
    return task, captured.get("files", {})


@pytest.mark.parametrize("lang", ["python", "r"])
def test_detached_task_carries_no_controller_path(tmp_path, monkeypatch, lang):
    import json as _json
    task, files = _detached_task(tmp_path, monkeypatch, lang)
    blob = _json.dumps(task) + "\n" + "\n".join(files.values())
    for root in _controller_roots():
        assert root not in blob, (
            f"detached {lang} task embeds controller path {root!r} — the node "
            f"shares no filesystem with this machine.\ntask={task}")
    # ARMED: prove we actually built something, so purity is not vacuous
    assert task.get("command") and task.get("site") == "siteA"
    assert "spec.json" in files, files.keys()


def test_detached_task_ships_no_aba_env(tmp_path, monkeypatch):
    """ABA_* vars name controller-side locations; the shared-fs lane forwards
    them ON PURPOSE, the detached lane must not."""
    task, _ = _detached_task(tmp_path, monkeypatch)
    assert not [k for k in (task.get("env_vars") or {}) if k.startswith("ABA_")], \
        task.get("env_vars")
