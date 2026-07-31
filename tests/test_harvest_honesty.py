"""Harvest honesty — outputs that evade the tracking contract are NAMED,
never silently lost.

The class (live + sweep, 2026-07-26): the harvest scans the run's working
tree within the block's time window, and three agent-reachable actions
defeated it with ZERO signal —
  1. chdir/setwd out of the tree (a live run's card showed 2 of 6 outputs;
     the rest existed untracked on a remote node);
  2. archive extraction / copy with preserved timestamps (files appear NOW
     but stamped OLD → the mtime window rejects them all);
  3. a background writer finishing between blocks (the gap between one
     block's harvest and the next block's start swallowed the files).

The contracts under guard:
  - the one-shot script lanes wrap agent code with a cwd probe; ending
    outside the tree yields a typed warning that names the REAL levers
    (register-by-absolute-path or WORK_DIR — NEVER keep_outputs, which is
    jobdir-scoped end to end and would silently keep nothing);
  - the harvest walker counts window rejects with the appeared-now/
    stamped-old signature (ctime >= window start, mtime < window start) and
    warns; files from EARLIER blocks fail both clocks and stay silent;
  - the kernel lane harvests from the END of the previous harvest, so
    gap files attach to the next block (tracked beats lost);
  - the kernel cwd marker is reconciled from the probe after every block —
    a drifted kernel warns and self-heals instead of lying forever;
  - the node-harness (detached) wrap carries the same probe and reports
    start/final dirs in its result contract.

Run: pytest tests/test_harvest_honesty.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="aba_hh_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ARTIFACTS_DIR", str(Path(_tmp) / "artifacts"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec.run import (cwd_probe_prologue, cwd_probe_epilogue,  # noqa: E402
                           read_cwd_sentinel, cwd_escape_warning,
                           harvest_artifacts)

pytestmark = pytest.mark.platform


# ── the probe primitives ─────────────────────────────────────────────────────

def _run_py(script: str, cwd: Path) -> None:
    subprocess.run([sys.executable, "-c", script], cwd=str(cwd), check=True)


def test_probe_reports_escape_and_warning_names_the_levers(tmp_path):
    scratch = tmp_path / "scratch"
    outside = tmp_path / "elsewhere"
    scratch.mkdir(); outside.mkdir()
    script = (cwd_probe_prologue("python")
              + f"import os\nos.chdir({str(outside)!r})\n"
              + "open('escaped.bin', 'wb').write(b'x')\n"
              + cwd_probe_epilogue("python"))
    _run_py(script, scratch)
    start, final = read_cwd_sentinel(scratch)
    assert Path(start).resolve() == scratch.resolve()   # armed: probe really ran
    assert Path(final).resolve() == outside.resolve()
    w = cwd_escape_warning(start, final)
    assert w and str(outside) in w
    assert "register_dataset" in w and "WORK_DIR" in w
    assert "keep_outputs" in w and "CANNOT be kept" in w, \
        "the warning must forbid the INVALID lever, not just name valid ones"
    # sentinel is consumed — a later read makes no stale claim
    assert read_cwd_sentinel(scratch) == (None, None)


def test_probe_quiet_for_in_tree_moves_and_no_move(tmp_path):
    scratch = tmp_path / "s"; (scratch / "sub").mkdir(parents=True)
    # in-tree chdir: harvest is recursive — NOT an escape
    script = (cwd_probe_prologue("python")
              + "import os\nos.chdir('sub')\n" + cwd_probe_epilogue("python"))
    _run_py(script, scratch)
    assert cwd_escape_warning(*read_cwd_sentinel(scratch)) is None
    # no move at all
    _run_py(cwd_probe_prologue("python") + "pass\n"
            + cwd_probe_epilogue("python"), scratch)
    assert cwd_escape_warning(*read_cwd_sentinel(scratch)) is None
    # script died before the epilogue → no sentinel → no claim either way
    assert cwd_escape_warning(*read_cwd_sentinel(scratch)) is None


def test_one_shot_lane_wraps_and_surfaces_the_warning(tmp_path, monkeypatch):
    """End to end through run_python_code: agent code escapes; the result's
    warning channel carries the typed escape note. Hermetic: the 'interpreter'
    is this python; the platform preamble tolerates a plain env."""
    from core.exec import run as runmod
    outside = tmp_path / "away"; outside.mkdir()
    code = (f"import os\nos.chdir({str(outside)!r})\n"
            f"open('lost.bin','wb').write(b'y')\nprint('done')\n")
    out = runmod.run_python_code(code, project_id="default",
                                 run_id="t-escape", interp=sys.executable,
                                 timeout_s=60)
    assert out.get("returncode") == 0, out
    warns = out.get("figure_warnings") or []
    assert any("OUTSIDE the run's tracked directory" in w for w in warns), \
        f"escape must surface in the one-shot result: {warns}"
    assert not (out.get("plots") or out.get("tables") or out.get("files")), \
        "nothing was harvested — that is exactly why the warning must exist"


def test_one_shot_lane_quiet_on_normal_run(tmp_path):
    from core.exec import run as runmod
    out = runmod.run_python_code("open('kept.txt','w').write('ok')\n",
                                 project_id="default", run_id="t-normal",
                                 interp=sys.executable, timeout_s=60)
    assert out.get("returncode") == 0
    warns = out.get("figure_warnings") or []
    assert not any("OUTSIDE" in w for w in warns), \
        "a normal run must gain ZERO escape noise"
    assert any(f.get("original_name") == "kept.txt"
               for f in out.get("files") or [])


def test_r_lane_composes_probe_in_the_right_order():
    """Seam pin for the R one-shot (behavioral parity needs an R runtime the
    hermetic suite doesn't assume): the prologue must sit AFTER the
    platform's setwd preamble — recording the dir the platform CHOSE, not
    the launch dir — and the epilogue last."""
    import re
    src = (Path(_BACKEND) / "core/exec/run.py").read_text()
    m = re.search(r'\(scratch / "script\.R"\)\.write_text\((.*?)\)\n', src, re.S)
    assert m, "R compose site not found"
    compose = m.group(1)
    assert compose.index("preamble") < compose.index('cwd_probe_prologue("r")')
    assert compose.index('cwd_probe_prologue("r")') < compose.index("code")
    assert compose.rindex('cwd_probe_epilogue("r")') > compose.index("code")
    # and the R probe strings are well-formed R
    assert ".aba_start_dir <- getwd()" in cwd_probe_prologue("r")
    assert "writeLines" in cwd_probe_epilogue("r")


# ── the stale-stamp window counter ───────────────────────────────────────────

def test_preserved_mtime_writes_warn_instead_of_vanishing(tmp_path):
    """The archive-extraction signature: a file APPEARS during the window but
    carries an OLD content stamp → counted and warned, not silently dropped."""
    scratch = tmp_path / "w"; scratch.mkdir()
    since = time.time() - 0.01
    f = scratch / "extracted.csv"
    f.write_text("a,b\n1,2\n")
    old = since - 3600
    os.utime(f, (old, old))               # preserve-timestamps copy/extract
    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=since)
    assert not tables, "the mtime window rejects it (current contract)"
    (w,) = [w for w in warns if "OLDER than the step start" in w]
    assert "extracted.csv" in w and "NOT" in w


def test_prior_block_files_stay_silent(tmp_path):
    """WIDE / the other side: files from an earlier block fail BOTH clocks
    (old ctime AND old mtime) — the counter must not cry wolf about them.

    The fixture stamps the file a full second back: whole-second filesystems
    (BeeGFS/NFS) cannot represent a sub-second gap at all, so "belongs to an
    earlier block" has to be expressed at the coarsest resolution the window
    compares at — see _window_floor in core/exec/run.py."""
    scratch = tmp_path / "w2"; scratch.mkdir()
    f = scratch / "earlier.csv"
    f.write_text("a\n1\n")                  # both clocks stamped in THIS second
    # Cross into the next whole second so the file is earlier at the resolution
    # the window compares at (os.utime would refresh ctime and defeat the point).
    time.sleep(1.0 - (time.time() % 1.0) + 0.02)
    since = time.time()                    # window opens AFTER the file existed
    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=since)
    assert not tables
    assert not any("OLDER than the step start" in w for w in warns), \
        f"prior-block files must not trigger the stale warning: {warns}"


def test_same_second_output_survives_whole_second_filesystem(tmp_path):
    """The BeeGFS/NFS drop: a 1-second-granularity filesystem truncates an
    output's mtime DOWN, so a step that started mid-second sorts its own
    fresh output below the window and harvests nothing — silently.

    Simulated on any filesystem by stamping the file at the whole second the
    fractional window start falls in (exactly what a coarse FS records)."""
    scratch = tmp_path / "cs"; scratch.mkdir()
    since = float(int(time.time())) + 0.45      # step starts mid-second
    f = scratch / "out.csv"
    f.write_text("a,b\n1,2\n")
    whole = float(int(since))                   # what the filesystem stores
    os.utime(f, (whole, whole))
    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=since)
    assert any(t.get("original_name") == "out.csv" for t in tables), \
        "a same-second output must not be dropped by stamp truncation"


def test_previous_second_file_still_excluded(tmp_path):
    """The ceiling for the fix above: widening the window to whole-second
    resolution must not swallow a file stamped in an EARLIER second."""
    scratch = tmp_path / "cs2"; scratch.mkdir()
    since = float(int(time.time())) + 0.45
    f = scratch / "before.csv"
    f.write_text("a\n1\n")
    prev = float(int(since)) - 1.0              # one whole second earlier
    os.utime(f, (prev, prev))
    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=since)
    assert not any(t.get("original_name") == "before.csv" for t in tables), \
        "the window must still exclude files stamped in a previous second"


# ── the kernel gap window + marker reconcile ─────────────────────────────────

class _FakeSess:
    """Jupyter-shaped session: no work_dir, marker attrs only."""
    def __init__(self):
        self._aba_cwd = None


def test_kernel_gap_window_attaches_between_block_files(tmp_path):
    """A file landing BETWEEN two blocks (after block N's harvest, before
    block N+1's start) is harvested by N+1 — tracked beats lost."""
    from content.bio.tools import run_exec as rx
    scratch = tmp_path / "k"; scratch.mkdir()
    sess = _FakeSess()
    # block N: harvest at t0, nothing there
    t0 = time.time()
    harvest_artifacts(scratch, since_ts=t0)
    sess._aba_last_harvest_ts = t0
    time.sleep(0.05)
    (scratch / "late.csv").write_text("x\n")     # the gap write
    time.sleep(0.05)
    start_n1 = time.time()                        # block N+1 starts AFTER it
    # OLD contract: since_ts=start_n1 → lost. NEW: since last harvest end.
    since = getattr(sess, "_aba_last_harvest_ts", None) or start_n1
    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=since)
    assert any(t.get("original_name") == "late.csv" for t in tables), \
        "the gap file must attach to the next block, not vanish"
    # and the wiring uses exactly this window: seam pin on both kernel lanes
    import re
    src = (Path(_BACKEND) / "content/bio/tools/run_exec.py").read_text()
    assert src.count('getattr(sess, "_aba_last_harvest_ts", None) or start_ts') >= 2, \
        "kernel lanes no longer harvest from the previous harvest's end"


def test_reconcile_updates_marker_and_warns_once(tmp_path):
    from content.bio.tools.run_exec import _reconcile_kernel_cwd, _with_cwd_probe
    scratch = tmp_path / "kc"; scratch.mkdir()
    away = tmp_path / "away"; away.mkdir()
    sess = _FakeSess(); sess._aba_cwd = str(scratch)
    # simulate the kernel block: wrapped code runs in a subprocess whose cwd
    # is the kernel's current dir; it escapes
    wrapped = _with_cwd_probe(sess, f"import os\nos.chdir({str(away)!r})\n",
                              "python", scratch)
    assert "getcwd" in wrapped                    # probe attached (jupyter shape)
    _run_py(wrapped, scratch)
    warns = _reconcile_kernel_cwd(sess, scratch)
    assert warns and str(away) in warns[0]
    assert sess._aba_cwd == str(Path(away))       # marker records the TRUTH
    # weft-shaped session (work_dir set): probe and reconcile both no-op —
    # its block protocol breaks loudly on chdir and needs no probe
    class _Weft:
        work_dir = str(scratch)
    assert _with_cwd_probe(_Weft(), "x=1", "python", scratch) == "x=1"
    assert _reconcile_kernel_cwd(_Weft(), scratch) == []


# ── the detached node harness ────────────────────────────────────────────────

def test_detached_harness_reports_final_cwd(tmp_path):
    """The node-side wrap: user script escapes; result.json carries
    start_cwd/final_cwd and the wrapped script never leaks into outputs."""
    import shutil
    entry = Path(_BACKEND) / "core/jobs/detached_entry.py"
    work = tmp_path / "job"; (work / "payload").mkdir(parents=True)
    away = tmp_path / "node-home"; away.mkdir()
    shutil.copyfile(entry, work / "payload" / "aba_entry.py")
    (work / "payload" / "user_code.py").write_text(
        f"import os\nos.chdir({str(away)!r})\n"
        f"open('remote-lost.bin','wb').write(b'z')\nprint('ok')\n")
    (work / "payload" / "spec.json").write_text(
        '{"interpreter": "python3", "script": "user_code.py", '
        '"job_id": "t1", "timeout_s": 60}')
    subprocess.run([sys.executable, "payload/aba_entry.py"], cwd=str(work),
                   check=True, capture_output=True)
    import json
    res = json.loads((work / "result.json").read_text())
    assert res["status"] == "ok"
    assert Path(res["start_cwd"]).resolve() == work.resolve()
    assert Path(res["final_cwd"]).resolve() == away.resolve()
    assert not any(o.startswith("._aba_wrapped") for o in res["outputs"]), \
        "the harness wrapper must not surface as an output"
    # controller-side: the poll seam turns those fields into the typed warning
    w = cwd_escape_warning(res["start_cwd"], res["final_cwd"])
    assert w and "register_dataset" in w


def test_detached_harness_accepts_a_future_import_script(tmp_path):
    """`from __future__` must be the FIRST statement in a Python file, so
    prepending the cwd probe turned a valid script into a SyntaxError. The
    probe goes after the future block instead."""
    import shutil
    import json
    entry = Path(_BACKEND) / "core/jobs/detached_entry.py"
    work = tmp_path / "job2"; (work / "payload").mkdir(parents=True)
    shutil.copyfile(entry, work / "payload" / "aba_entry.py")
    (work / "payload" / "user_code.py").write_text(
        '"""A module docstring, then the future import."""\n'
        "from __future__ import annotations\n"
        "import pathlib\n"
        "pathlib.Path('out.txt').write_text('done')\n")
    (work / "payload" / "spec.json").write_text(
        '{"interpreter": "python3", "script": "user_code.py", '
        '"job_id": "t2", "timeout_s": 60}')
    subprocess.run([sys.executable, "payload/aba_entry.py"], cwd=str(work),
                   check=True, capture_output=True)
    res = json.loads((work / "result.json").read_text())
    assert res["status"] == "ok", res
    assert res.get("start_cwd"), "the probe must still run"
    assert (work / "out.txt").exists(), "the payload must actually execute"


# ── out-of-band env installs (sweep item D): the identity tripwire ──────────

def _env_row_world(monkeypatch, prefix: Path):
    """Stub project_env's internals: one in-memory registry row, a counting
    fake substrate snapshot, a session pointing at `prefix`."""
    import core.compute.project_env as pe
    row = {"rev": 3, "additions": [], "snapshot": {}}
    calls = {"n": 0}
    monkeypatch.setattr(pe, "ensure", lambda pid, lang: {
        "session_id": "ses_t", "runtime": {"prefix": str(prefix)}})
    monkeypatch.setattr(pe, "get", lambda pid, lang: row)
    monkeypatch.setattr(pe, "_save_row", lambda pid, lang, r: None)

    class _Ad:
        def session_snapshot(self, sid, name=""):
            calls["n"] += 1
            return {"env_id": f"env:v1:snap{calls['n']}"}
    monkeypatch.setattr(pe._adapter, "get_compute", lambda: _Ad())
    monkeypatch.setattr(pe.named_envs, "_sync", lambda x: x)
    return pe, row, calls


def _mk_prefix(base: Path, *dists: str) -> Path:
    sp = base / "lib" / "python3.12" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    for d in dists:
        (sp / f"{d}.dist-info").mkdir(exist_ok=True)
    return base


def test_out_of_band_install_dirties_the_snapshot_cache(tmp_path, monkeypatch):
    prefix = _mk_prefix(tmp_path / "pfx", "pkg_a-1.0")
    pe, row, calls = _env_row_world(monkeypatch, prefix)
    eid1 = pe.snapshot("p1", "python")
    assert eid1 == "env:v1:snap1" and calls["n"] == 1
    # unchanged prefix → dirty-cache hit, no substrate round trip
    assert pe.snapshot("p1", "python") == eid1 and calls["n"] == 1
    # agent code pip-installs OUT OF BAND: prefix changes, rev does not
    _mk_prefix(prefix, "rogue_pkg-2.0")
    eid2 = pe.snapshot("p1", "python")
    assert eid2 == "env:v1:snap2" and calls["n"] == 2, \
        "a mutated prefix must re-snapshot, not serve the stale identity"
    # the event is RECORDED — the registry stays honest about what it
    # cannot describe — and the rev moved with it
    (ev,) = [a for a in row["additions"] if a.get("eco") == "out-of-band"]
    assert "outside the platform install verbs" in ev["note"]
    assert row["rev"] == 4
    # and the new signature is remembered: quiet again until the next change
    assert pe.snapshot("p1", "python") == eid2 and calls["n"] == 2


def test_out_of_band_marker_is_skipped_by_the_session_replay():
    """The marker row carries no `specs` — the session-rebuild replay must
    skip it BEFORE touching add['specs'] (a real rebuild would otherwise
    KeyError far from any bench). Source pin on the ordering, since the
    rebuild path needs a live substrate to exercise behaviorally."""
    import re
    src = (Path(_BACKEND) / "core/compute/project_env.py").read_text()
    loop = re.search(r"for add in additions:.*?# installs are the FLIP",
                     src, re.S)
    assert loop, "replay loop not found"
    body = loop.group(0)
    assert '"out-of-band"' in body, "replay no longer skips the marker rows"
    assert body.index('"out-of-band"') < body.index('add["specs"]')


def test_tripwire_abstains_without_a_statable_prefix(tmp_path, monkeypatch):
    """WIDE: activation-only topology (no prefix) and legacy rows (no
    recorded signature) must serve the cache exactly as before — the
    tripwire abstains rather than guessing."""
    import core.compute.project_env as pe
    assert pe._prefix_signature(None, "python") is None
    assert pe._prefix_signature({"prefix": str(tmp_path / "empty")},
                                "python") is None
    prefix = _mk_prefix(tmp_path / "pfx2", "pkg_a-1.0")
    pe2, row, calls = _env_row_world(monkeypatch, prefix)
    # legacy row: cached snapshot WITHOUT prefix_sig → cache honoured
    row["snapshot"] = {"env_id": "env:v1:legacy", "at_rev": 3}
    assert pe2.snapshot("p1", "python") == "env:v1:legacy"
    assert calls["n"] == 0


if __name__ == "__main__":
    import subprocess as _sp
    raise SystemExit(_sp.call([sys.executable, "-m", "pytest", __file__, "-v"]))
