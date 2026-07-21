"""Five small defects a live session surfaced (2026-07-21), one guard each.

Every one of them is a case where the system had the right answer available and
threw it away — a recorded name never consulted, a failure rendered as a success,
a typed refusal swallowed. Cheap to fix, expensive to diagnose from the outside.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio


# ── 2: a failed tool call must not render like a successful one ──────────────

def test_feed_summary_shows_errors():
    from content.bio.tools import _summ_result
    s = _summ_result({"error": "artifact not found for path 'markers_dotplot.png'"})
    assert "error=" in s, f"a failure rendered as {s!r} — indistinguishable from success"
    assert "not found" in s


def test_feed_summary_distinguishes_unlisted_from_empty():
    """An unrecognized return shows its SHAPE; only a genuinely empty dict is `{}`."""
    from content.bio.tools import _summ_result
    vision = _summ_result({"id": None, "type": "figure", "title": "x",
                           "artifact_path": "/artifacts/p/a.png", "_vision_blocks": []})
    assert vision != "{}", "an unlisted success still renders as an empty result"
    assert "keys=" in vision
    assert _summ_result({}) == "{}", "a truly empty result should stay `{}`"


def test_feed_summary_error_is_not_truncated_to_uselessness():
    from content.bio.tools import _summ_result
    long = "artifact not found for path 'x.png' (looked under the project's work area)"
    assert len(_summ_result({"error": long})) > 40


# ── 1: a produced name must resolve to the served copy ──────────────────────

def test_find_by_produced_name_matches_leaf_and_full_relpath(monkeypatch):
    """Harvest stores the served copy under a generated id and records the human
    name as `original_name` (with its producing subdir). A caller holding the bare
    leaf must still resolve — that is the whole point of the index."""
    from core.exec import artifacts as A

    # Patch the store API, not the raw connection: `_conn` is confined to
    # exec_records by the store-port invariant, and a test that reaches past the
    # API repeats the very violation the code was corrected for.
    monkeypatch.setattr(A.exec_records, "list_recent_exec_ids",
                        lambda limit=200: ["ex2", "ex1"])      # newest first
    monkeypatch.setattr(A, "list_artifacts", lambda ex, kind=None: {
        "ex2": [{"original_name": "run_b/markers_dotplot.png", "url": "/artifacts/p/bbb.png"}],
        "ex1": [{"original_name": "qc.png", "url": "/artifacts/p/aaa.png"}],
    }.get(ex, []))

    by_leaf = A.find_by_produced_name("markers_dotplot.png")
    assert [a["url"] for a in by_leaf] == ["/artifacts/p/bbb.png"], by_leaf
    assert A.find_by_produced_name("run_b/markers_dotplot.png")      # full relpath too
    assert A.find_by_produced_name("qc.png")[0]["url"] == "/artifacts/p/aaa.png"
    assert A.find_by_produced_name("nope.png") == []
    assert A.find_by_produced_name("") == []


def test_find_by_produced_name_never_raises(monkeypatch):
    """It sits on a resolution path — a broken index must degrade to 'no match'."""
    from core.exec import artifacts as A

    def _boom(limit=200):
        raise RuntimeError("index gone")
    monkeypatch.setattr(A.exec_records, "list_recent_exec_ids", _boom)
    assert A.find_by_produced_name("x.png") == []


# ── 4: Bioconductor is a repository, not a bespoke installer ────────────────

def test_bioc_repos_are_ordered_and_release_pinned(monkeypatch):
    from content.bio.tools import discovery
    monkeypatch.setenv("ABA_BIOC_RELEASE", "3.21")
    repos = discovery._bioc_repos()
    assert repos[0].endswith("/3.21/bioc"), repos
    assert any("annotation" in r for r in repos) and any("experiment" in r for r in repos)
    assert all(r.startswith("https://") for r in repos)


def test_bioconductor_goes_through_the_cran_lane_with_repos(monkeypatch):
    """It must reach the cran lane WITH cran_repos — that is what makes it layer
    on an adopted base instead of needing BiocManager and a writable prefix."""
    from content.bio.tools import discovery
    seen: dict = {}

    def _fake_cran_lane(pid, spec, *, repos=None):
        seen.update({"spec": spec, "repos": repos})
        return True

    monkeypatch.setattr(discovery, "_cran_lane", _fake_cran_lane)
    monkeypatch.setattr(discovery, "_r_version_in_session", lambda *_a, **_k: None)
    fake_pe = types.SimpleNamespace(
        install=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no conda build")),
        run_installer=lambda *a, **k: {"ok": True})
    monkeypatch.setitem(sys.modules, "core.compute",
                        types.SimpleNamespace(project_env=fake_pe))
    cap = {"name": "SomePkg",
           "provisioning": {"r": {"source": "bioconductor", "package": "SomePkg"}}}
    try:
        discovery._ensure_r_via_session(cap, {}, None, "SomePkg")
    except Exception:  # noqa: BLE001 — post-install verification is out of scope
        pass
    assert seen.get("spec") == "SomePkg"
    assert seen.get("repos"), "bioconductor reached the cran lane without cran_repos"
    assert any("bioconductor.org" in r for r in seen["repos"])


# ── 5: the manifest's store entry claims only what it computes ──────────────

def test_store_entry_has_no_phantom_size_field():
    from content.bio.lifecycle import runs
    outs = [{"label": "out/x.zarr/zarr.json", "kind": "file", "size": "1.2 MB"},
            {"label": "out/x.zarr/c/0", "kind": "file", "size": "8.0 MB"},
            {"label": "fig.png", "kind": "figure"}]
    collapsed = runs._collapse_store_members(outs, "run_1")
    store = [o for o in collapsed if o.get("kind") == "store"]
    assert len(store) == 1 and store[0]["n_members"] == 2
    assert "_bytes" not in store[0], "internal accumulator leaked into the manifest"
    assert "size" not in store[0], "store claims a size it never computed"
    assert any(o.get("label") == "fig.png" for o in collapsed), "non-store output dropped"


# ── 3: a mount-scoped R base must not silently lose the .rds bridge ─────────

def _shim_for(monkeypatch, tmp_path, rt):
    from content.bio.viewers.launchers import pagoda3
    import core.compute.project_env as pe
    import core.config as cfg
    monkeypatch.setattr(pe, "runtime", lambda pid, lang: rt)
    monkeypatch.setattr(cfg, "project_work_dir", lambda pid: tmp_path)
    return pagoda3._rscript_shim("p1")


def test_no_shim_when_the_interpreter_is_directly_execable(monkeypatch, tmp_path):
    assert _shim_for(monkeypatch, tmp_path,
                     {"direct_exec": True, "prefix": "/opt/renv"}) is None


def test_shim_forwards_arguments_into_the_activated_shell(monkeypatch, tmp_path):
    """`bash -c <script> <argv0> "$@"` — words after the script become $0,$1,... for
    it, so a naive `"$@"` appended OUTSIDE the quoted script hands the arguments to
    bash instead of to Rscript."""
    import os
    p = _shim_for(monkeypatch, tmp_path,
                  {"direct_exec": False, "activation": "source /mnt/act.sh",
                   "ns_wrap": False})
    assert p and Path(p).exists()
    body = Path(p).read_text()
    assert 'exec Rscript "$@"' in body, body
    assert body.rstrip().endswith('aba-rscript-shim "$@"'), body
    assert "source /mnt/act.sh" in body
    assert os.access(p, os.X_OK), "shim is not executable — lstar execs this path"


def test_shim_keeps_the_namespace_wrapper(monkeypatch, tmp_path):
    """A squashfs base's mounts exist only inside the namespace; dropping the
    wrapper would run Rscript where the prefix isn't mounted."""
    body = Path(_shim_for(monkeypatch, tmp_path,
                          {"direct_exec": False, "activation": "source /mnt/act.sh",
                           "ns_wrap": True})).read_text()
    assert "unshare -rm bash -c" in body, body


def test_rscript_falls_back_to_the_shim_and_says_so(monkeypatch, tmp_path, capsys):
    """`interpreter()` raises a typed refusal on a mount-scoped base. Swallowing it
    silently dropped the bridge with no signal — the live failure mode."""
    from content.bio.viewers.launchers import pagoda3
    import core.compute.base_env as be
    import core.compute.project_env as pe
    import core.config as cfg
    monkeypatch.setattr(be, "active", lambda *_a, **_k: True)
    monkeypatch.setattr(pe, "interpreter",
                        lambda *_a, **_k: (_ for _ in ()).throw(
                            RuntimeError("session.no_direct_exec")))
    monkeypatch.setattr(pe, "runtime", lambda pid, lang: {
        "direct_exec": False, "activation": "source /mnt/act.sh", "ns_wrap": False})
    monkeypatch.setattr(cfg, "project_work_dir", lambda pid: tmp_path)
    monkeypatch.delenv("LSTAR_RSCRIPT", raising=False)
    got = pagoda3._rscript("p1")
    assert got and Path(got).exists(), "bridge silently lost on a mount-scoped base"
    assert "not directly execable" in capsys.readouterr().out
