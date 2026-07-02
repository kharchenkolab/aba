"""Versioned-release substrate + pin-on-launch + the skew/upgrade matrix (misc/slim_sif_deploy.md).

Phase 3 = the release ops (promote/rollback/gc/resolve, pin). Phase 4 = the forward-thinking
adversarial matrix (§2 skew modes made into tests): upgrade-during-session, atomic-swap-not-in-place,
session-vs-job skew, Nextflow auto-resume across upgrade, rollback, GC safety, custom-NF upgrade —
plus the invariant that WITHOUT $ABA_SHARE (personal / fat SIF) everything is a no-op.

Run: .venv/bin/python -m pytest tests/test_release_lifecycle.py -q
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_release_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()
from core import release as R  # noqa: E402
from core.graph.jobs import create_job  # noqa: E402


def _setup_share(tmp: Path, vers=("v1", "v2")) -> str:
    share = tmp / "ABA_SHARE"
    (share / "releases").mkdir(parents=True, exist_ok=True)
    for v in vers:
        src = tmp / f"inst-{v}"; src.mkdir(exist_ok=True)
        R.build_mock(v, str(src), share=str(share))
    return str(share)


# ─────────────────────────── Phase 3 — release ops ───────────────────────────

def test_promote_resolve_list(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path))
    assert set(R.list_releases()) == {"v1", "v2"}
    assert R.resolve_current() is None                 # no current yet
    R.promote("v1"); assert R.resolve_current() == "v1"
    R.promote("v2"); assert R.resolve_current() == "v2"
    assert R.release_path("v1") and R.release_path("v2")


def test_promote_rejects_unbuilt(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path))
    try:
        R.promote("v999"); assert False, "should refuse to promote an unbuilt release"
    except FileNotFoundError:
        pass


def test_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path))
    R.promote("v1"); R.promote("v2"); assert R.resolve_current() == "v2"
    R.rollback(); assert R.resolve_current() == "v1"    # instant recovery to the prior release


# ────────────────── Phase 4 — the skew / upgrade matrix ──────────────────

def test_atomic_swap_leaves_old_tree_intact(tmp_path, monkeypatch):
    # (§2.1/§2.2) upgrade during a running job: promote must build+swap, NEVER mutate the old tree
    # a running job still holds.
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path))
    R.promote("v1")
    old = R.release_path("v1"); before = (old / "manifest.json").read_text()
    R.promote("v2")
    assert R.release_path("v1") and (R.release_path("v1") / "manifest.json").read_text() == before


def test_pin_on_launch_survives_upgrade(tmp_path, monkeypatch):
    # (§2.3) a session pinned to v1 keeps v1 after the admin promotes v2; a fresh process gets v2.
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path)); R.promote("v1")
    monkeypatch.setenv("ABA_RELEASE_ID", "v1")          # this session pinned at launch
    R.promote("v2")                                     # admin upgrades under it
    assert R.active_release_id() == "v1"                # pinned session unaffected
    monkeypatch.delenv("ABA_RELEASE_ID")
    assert R.active_release_id() == "v2"                # a fresh, unpinned process resolves current


def test_stamp_release_pins_to_session_not_current(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path)); R.promote("v2")
    monkeypatch.setenv("ABA_RELEASE_ID", "v1")          # session launched on v1, current is now v2
    assert R.stamp_release({"a": 1})["release_id"] == "v1"          # pins to the SESSION's release
    assert R.stamp_release({"release_id": "vX"})["release_id"] == "vX"  # never overwrites explicit


def test_nextflow_auto_resume_keeps_pinned_release(tmp_path, monkeypatch):
    # (§2 sharp case) a head submitted on v1, resumed after an upgrade, must re-run on v1 — else the
    # -resume task hashes shift and the cache silently invalidates. release_id persists in params,
    # exactly as _maybe_resume_nextflow_job reuses them ({**params, nf_resumes+1}).
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path)); R.promote("v1")
    monkeypatch.setenv("ABA_RELEASE_ID", "v1")
    params = R.stamp_release({"pipeline": "nf-core/rnaseq", "run_id": "r1"})
    R.promote("v2")                                     # upgrade mid-run
    resumed = {**params, "nf_resumes": 1, "slurm_id": None}   # the resume re-submit
    assert resumed["release_id"] == "v1"                # stays pinned → cache stays valid


def test_gc_protects_current_prev_and_referenced(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path, vers=("v1", "v2", "v3", "v4")))
    R.promote("v1"); R.promote("v2"); R.promote("v3"); R.promote("v4")   # current=v4, prev=v3
    out = R.gc(keep=0, referenced=("v1",))              # v1 pinned by a live session
    assert out["removed"] == ["v2"], out                # only the truly-unreferenced one
    assert R.release_path("v1") and R.release_path("v3") and R.release_path("v4")
    assert not R.release_path("v2")


def test_custom_nextflow_upgrade_is_release_scoped(tmp_path, monkeypatch):
    # a job pinned to v1 resolves v1's tree (its NF); new jobs resolve current — so a custom-NF
    # bump lands in a new release without disturbing pinned in-flight jobs.
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path)); R.promote("v2")
    assert R.release_path("v1") != R.release_path("v2")
    monkeypatch.setenv("ABA_RELEASE_ID", "v1")
    assert R.release_path(R.active_release_id()) == R.release_path("v1")   # pinned → old NF tree


def test_no_op_without_share(tmp_path, monkeypatch):
    # personal install / fat SIF: no $ABA_SHARE → resolver/pin are inert, jobs carry no release_id.
    monkeypatch.delenv("ABA_SHARE", raising=False)
    monkeypatch.delenv("ABA_RELEASE_ID", raising=False)
    assert R.resolve_current() is None and R.active_release_id() is None
    assert R.stamp_release({"a": 1}) == {"a": 1}        # unchanged


def test_create_job_pins_release_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path)); R.promote("v1")
    monkeypatch.setenv("ABA_RELEASE_ID", "v1")
    j = create_job("job_rel1", "run_nextflow", "t", None, {"pipeline": "x"})
    assert (j.get("params") or {}).get("release_id") == "v1"      # persisted job carries the pin
    monkeypatch.delenv("ABA_RELEASE_ID"); monkeypatch.delenv("ABA_SHARE")
    j2 = create_job("job_rel2", "run_python", "t", None, {"code": "1"})
    assert "release_id" not in (j2.get("params") or {})           # personal/fat: no pin


# ────────────────── operational-completeness layer ──────────────────

def test_version_ordering_is_numeric_not_lexical(tmp_path, monkeypatch):
    # 2024.11.0 must sort AFTER 2024.9.0 (lexical would put "11" before "9"); newest = 2025.1.0.
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path, vers=("2024.9.0", "2024.11.0", "2025.1.0")))
    assert R.list_releases() == ["2024.9.0", "2024.11.0", "2025.1.0"]
    for v in ("2024.9.0", "2024.11.0", "2025.1.0"):
        R.promote(v)
    out = R.gc(keep=1, referenced=())          # keep only the newest BY VERSION
    assert out["removed"] == ["2024.9.0"] and R.release_path("2025.1.0"), out   # not the lexical-last


def test_read_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path, vers=("v1",)))
    mf = R.read_manifest("v1")
    assert mf.get("version") == "v1"           # build_mock wrote a manifest
    assert R.read_manifest("nope") == {}


def test_gc_uses_live_refcount_from_running_jobs(tmp_path, monkeypatch):
    # GC with NO explicit referenced must still protect a release a RUNNING job pins (the live
    # refcount) — e.g. a long Nextflow head submitted on an older release.
    import sqlite3, json as _j
    from core.config import PROJECTS_DIR
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path, vers=("v1", "v2", "v3")))
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    pd = PROJECTS_DIR / "prj_live"; pd.mkdir(exist_ok=True)
    c = sqlite3.connect(pd / "project.db")
    c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, title TEXT, status TEXT, "
              "focus_entity_id TEXT, params TEXT, log_tail TEXT, error TEXT, created_at TEXT NOT NULL, "
              "started_at TEXT, finished_at TEXT)")
    c.execute("INSERT INTO jobs (id,kind,status,params,created_at) VALUES (?,?,?,?,?)",
              ("job_live", "run_nextflow", "running", _j.dumps({"release_id": "v1"}),
               "2026-07-02T00:00:00+00:00"))
    c.commit(); c.close()
    R.promote("v3")                             # current=v3; a running job still pins v1
    refs = R.compute_referenced()
    assert "v1" in refs and "v3" in refs, refs
    out = R.gc(keep=0)                          # no explicit referenced → live scan
    assert "v1" not in out["removed"] and R.release_path("v1"), out   # protected by the running job
    (pd / "project.db").unlink()                # don't leak into later tests' live scan


def test_verify_structural_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", _setup_share(tmp_path, vers=("v1",)))
    r = R.verify("v1")
    assert r["ok"] and r["checks"]["has_manifest"] and r["checks"]["manifest_version_matches"], r
    bad = R.verify("v999")                      # unbuilt → not ok
    assert not bad["ok"]


# ────────────────── content-addressed components (Phase 1) ──────────────────

def test_ensure_component_dedups(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_SHARE", str(tmp_path / "share"))
    calls = []
    def builder(d):
        calls.append(1); (Path(d) / "marker").write_text("x")
    p1 = R.ensure_component("env", "h1", builder)
    p2 = R.ensure_component("env", "h1", builder)     # same id → reuse, builder NOT re-run
    assert p1 == p2 and (p1 / "marker").exists() and len(calls) == 1


def test_code_only_upgrade_reuses_env_component(tmp_path, monkeypatch):
    # THE headline: a code-only upgrade re-links the same (multi-GB) env component — zero copy.
    share = tmp_path / "share"; monkeypatch.setenv("ABA_SHARE", str(share))
    R.ensure_component("env", "envH1", lambda d: (Path(d) / "python").write_text("big env"))
    R.ensure_component("opt", "optH1", lambda d: (Path(d) / "nextflow").write_text("nf"))
    R.ensure_component("repo", "sha_A", lambda d: (Path(d) / "app.py").write_text("v1"))
    R.ensure_component("repo", "sha_B", lambda d: (Path(d) / "app.py").write_text("v2"))
    R.compose_release("2026.07.02-A", {"repo": "sha_A", "env": "envH1", "opt": "optH1"})
    R.compose_release("2026.07.02-B", {"repo": "sha_B", "env": "envH1", "opt": "optH1"})  # code-only
    envs = list((share / "components" / "env").iterdir())
    assert len(envs) == 1 and envs[0].name == "envH1", "code-only upgrade must NOT copy the env"
    a = (share / "releases" / "2026.07.02-A" / "env").resolve()
    b = (share / "releases" / "2026.07.02-B" / "env").resolve()
    assert a == b == (share / "components" / "env" / "envH1").resolve()          # shared by reference
    assert R.release_components("2026.07.02-A")["repo"] == "sha_A"
    assert R.release_components("2026.07.02-B")["repo"] == "sha_B"               # differ only in code


def test_hash_files_content_addressed(tmp_path):
    a = tmp_path / "environment.yml"; a.write_text("deps: [numpy]")
    b = tmp_path / "r-environment.yml"; b.write_text("r: [seurat]")
    h1 = R.hash_files([str(a), str(b)])
    assert h1 == R.hash_files([str(a), str(b)])        # stable
    a.write_text("deps: [numpy, scipy]")
    assert R.hash_files([str(a), str(b)]) != h1        # changed lockfile → new component id


def test_gc_sweeps_orphaned_components(tmp_path, monkeypatch):
    share = tmp_path / "share"; monkeypatch.setenv("ABA_SHARE", str(share))
    for cid, txt in [("envOld", "old"), ("envNew", "new")]:
        R.ensure_component("env", cid, (lambda t: (lambda d: (Path(d) / "x").write_text(t)))(txt))
    R.ensure_component("opt", "opt1", lambda d: (Path(d) / "x").write_text("o"))
    for cid in ("s1", "s2", "s3"):
        R.ensure_component("repo", cid, (lambda c: (lambda d: (Path(d) / "x").write_text(c)))(cid))
    R.compose_release("v1", {"repo": "s1", "env": "envOld", "opt": "opt1"})
    R.compose_release("v2", {"repo": "s2", "env": "envNew", "opt": "opt1"})
    R.compose_release("v3", {"repo": "s3", "env": "envNew", "opt": "opt1"})
    R.promote("v1"); R.promote("v2"); R.promote("v3")   # current=v3, prev=v2
    out = R.gc(keep=1, referenced=())                    # removes v1, then its now-orphaned components
    assert "v1" in out["removed"]
    assert "env/envOld" in out["components_removed"] and "repo/s1" in out["components_removed"]
    assert (share / "components" / "env" / "envNew").exists()          # still pinned by v2,v3
    assert not (share / "components" / "env" / "envOld").exists()


def test_stage_release_copies_composes_promotes(tmp_path, monkeypatch):
    # The deploy-side orchestrator: copy built artifacts into content-addressed components, compose,
    # promote. Mirrors what deploy.sh calls (slim uses kinds sif/env/opt).
    share = tmp_path / "share"; monkeypatch.setenv("ABA_SHARE", str(share))
    sif = tmp_path / "aba.sif"; sif.write_text("IMAGE-A")
    env_src = tmp_path / "base"; (env_src / "bin").mkdir(parents=True); (env_src / "bin" / "python").write_text("py")
    opt_src = tmp_path / "opt"; opt_src.mkdir(); (opt_src / "nextflow").write_text("nf")
    out = R.stage_release("2026.07.02-A", {
        "sif": ("codeA", str(sif)), "env": ("envH", str(env_src)), "opt": ("optH", str(opt_src))},
        do_promote=True)
    assert out["current"] == "2026.07.02-A" and not out["reused"]
    # components materialized + release composed through symlinks
    assert (share / "components" / "sif" / "codeA" / "aba.sif").read_text() == "IMAGE-A"
    assert (share / "releases" / "2026.07.02-A" / "env" / "bin" / "python").read_text() == "py"
    assert R.release_components("2026.07.02-A") == {"sif": "codeA", "env": "envH", "opt": "optH"}


def test_stage_release_code_only_upgrade_skips_env_copy(tmp_path, monkeypatch):
    # THE deploy-side win: re-staging with a NEW sif but the SAME env id does NOT re-copy the env.
    share = tmp_path / "share"; monkeypatch.setenv("ABA_SHARE", str(share))
    sifA = tmp_path / "a.sif"; sifA.write_text("A")
    sifB = tmp_path / "b.sif"; sifB.write_text("B")
    env_src = tmp_path / "base"; env_src.mkdir(); (env_src / "big").write_text("x" * 1000)
    R.stage_release("relA", {"sif": ("shaA", str(sifA)), "env": ("envH", str(env_src))}, do_promote=True)
    env_dir = share / "components" / "env" / "envH"
    mtime_before = (env_dir / "big").stat().st_mtime_ns
    # code-only upgrade: new sif, SAME env id
    out = R.stage_release("relB", {"sif": ("shaB", str(sifB)), "env": ("envH", str(env_src))}, do_promote=True)
    assert "env/envH" in out["reused"], out                    # env was reused, not re-copied
    assert (env_dir / "big").stat().st_mtime_ns == mtime_before, "env component must NOT be rewritten"
    assert len(list((share / "components" / "sif").iterdir())) == 2   # two code images
    assert len(list((share / "components" / "env").iterdir())) == 1   # ONE env, shared
    assert R.resolve_current() == "relB"


def test_compute_version_tag_else_datesha(tmp_path):
    import subprocess
    import datetime
    repo = tmp_path / "repo"; repo.mkdir()
    genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    def git(*a):
        subprocess.run(["git", *a], cwd=str(repo), capture_output=True, env=genv)
    git("init", "-q"); (repo / "f").write_text("x"); git("add", "."); git("commit", "-qm", "init")
    v = R.compute_version(str(repo), now=datetime.date(2026, 7, 2))
    assert v.startswith("2026.07.02-") and len(v) > len("2026.07.02-"), v   # date-sha, no tag
    git("tag", "v1.2.3")
    assert R.compute_version(str(repo)) == "v1.2.3"                          # a release tag wins


def test_preflight_release_image_pins_concrete_release(tmp_path, monkeypatch):
    # Phase 3 pin-on-launch: aba_preflight resolves current → the CONCRETE releases/<id> sif+env
    # (not via `current`), so a mid-session promote can't move an already-launched session.
    sys.path.insert(0, str(ROOT / "install" / "ood"))
    import aba_preflight
    share = tmp_path / "share"; monkeypatch.setenv("ABA_SHARE", str(share))

    def _sif(txt):
        return lambda d: (Path(d) / "aba-slim.sif").write_text(txt)
    def _env(d):
        (Path(d) / "aba-venv" / "bin").mkdir(parents=True)
        (Path(d) / "aba-venv" / "bin" / "python").write_text("py")
        (Path(d) / "aba-tools").mkdir()
    R.ensure_component("sif", "A", _sif("A")); R.ensure_component("env", "envA", _env)
    R.compose_release("relA", {"sif": "A", "env": "envA"}); R.promote("relA")

    env = aba_preflight.resolve_release_image(str(share))
    assert env["ABA_RELEASE_ID"] == "relA"
    assert "/releases/relA/" in env["ABA_SIF"] and env["ABA_SIF"].endswith("aba-slim.sif")
    assert "/releases/relA/" in env["ABA_BASE_DIR"] and env["ABA_BASE_DIR"].endswith("aba-venv")
    assert "/current/" not in env["ABA_SIF"]                       # concrete → pinned, not via current

    # admin promotes relB mid-session → the ALREADY-resolved env still points at relA (the pin)
    R.ensure_component("sif", "B", _sif("B"))
    R.compose_release("relB", {"sif": "B", "env": "envA"}); R.promote("relB")
    assert "/releases/relA/" in env["ABA_SIF"], "resolved session env must stay pinned to relA"
    # a NEW session (fresh resolve) picks up relB
    assert aba_preflight.resolve_release_image(str(share))["ABA_RELEASE_ID"] == "relB"
    # no versioned layout → {} (caller falls back to static site.yaml image paths)
    assert aba_preflight.resolve_release_image(str(tmp_path / "nolayout")) == {}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
