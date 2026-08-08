"""pagoda3 launcher — unified conversion (any lstar-supported source → .lstar.zarr
via the lstar CLI) + registry coverage. See misc/pagoda3_integration.md."""
import subprocess
import types

import pytest


def _convert_call(calls):
    """The `lstar convert` invocation among everything _convert_any runs.

    It is no longer the only subprocess: the lane now probes the produced
    store's counts basis afterwards (a `-c` metadata read), so a test that
    looked at "the last call" would be reading the probe."""
    return next(a for a in calls if "convert" in a)


def test_convert_any_optimizes_via_viewer_flag(monkeypatch, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    src, out = tmp_path / "processed.h5ad", tmp_path / "o.lstar.zarr.building"
    pagoda3._convert_any(src, out)
    a = _convert_call(calls)
    # sys.executable -m lstar convert <src> <out> --to store --viewer
    #   --to store forces store output despite the .building temp suffix;
    #   --viewer optimizes to viewer@0.1 in-process (lstar-sc >=0.1.7 auto-falls-back
    #   raw→lognorm) — node-free, no prep.ts.
    assert a[1:4] == ["-m", "lstar", "convert"]
    assert str(src) in a and str(out) in a
    assert "--to" in a and "store" in a
    assert a[-1] == "--viewer"


def test_convert_any_raises_with_stderr_on_failure(monkeypatch, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    monkeypatch.setattr(subprocess, "run",
                        lambda args, **kw: types.SimpleNamespace(
                            returncode=2, stdout="", stderr="unsupported source"))
    with pytest.raises(RuntimeError, match="unsupported source"):
        pagoda3._convert_any(tmp_path / "x.rds", tmp_path / "o.building")


def test_registry_matches_single_cell_source_formats():
    import content.bio  # noqa: F401 — load viewer registry
    from core.viewers.registry import viewers_for

    def top_external(name):
        vs = [v for v in viewers_for({"name": name}) if v.mode == "external"]
        return vs[0].open_external if vs else None

    # every supported source routes to the pagoda3 launcher
    assert top_external("processed.h5ad") == "pagoda3_launcher"
    assert top_external("multimodal.h5mu") == "pagoda3_launcher"
    assert top_external("seurat_obj.rds") == "pagoda3_launcher"    # Seurat/SCE/pagoda2/conos
    assert top_external("sample.lstar.zarr") == "pagoda3_launcher"
    # a non-single-cell file gets no external viewer
    assert top_external("table.csv") is None


def _locate_returning(monkeypatch, *paths):
    """Fake the project-locate door with a fixed hit list."""
    from content.bio import project_locate
    monkeypatch.setattr(project_locate, "locate_project_files",
                        lambda name, limit=6: {"matches": [{"path": str(p)} for p in paths]})


def test_resolve_source_falls_back_to_the_project_locate_door(monkeypatch, tmp_path):
    """A `.lstar.zarr` store a run wrote lands physically in work/<ana_id>/<name>,
    but the file tree may hand us only the LOGICAL output path (threads/.../output/)
    with no physical file there. _resolve_source must still find it by NAME
    (regression: 'pagoda3: source not found').

    This test used to stage a file under work/ and expect a private glob to
    sweep it up. That glob is gone — resolution goes through
    `locate_project_files`, precisely so a basename hit carries the project's
    own provenance instead of newest-wins across same-named files. The test kept
    asserting the old mechanism and had been red ever since; it now fakes the
    door and asserts the contract that replaced it."""
    from content.bio.viewers.launchers import pagoda3
    import core.config as cfg

    store = tmp_path / "work" / "ana_abc" / "processed.lstar.zarr"
    store.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_root", lambda pid: tmp_path)
    monkeypatch.setattr(cfg, "project_data_dir", lambda pid: tmp_path / "data")
    _locate_returning(monkeypatch, store)

    node = {"path": "threads/t/runs/r/output/processed.lstar.zarr",
            "name": "processed.lstar.zarr"}   # logical path, nothing on disk there
    assert pagoda3._resolve_source(node, "prj_x") == store


def test_resolve_source_refuses_an_AMBIGUOUS_name(monkeypatch, tmp_path):
    """CEILING on the fallback: two runs with the same output name must NOT be
    resolved by picking one. Ambiguity falls through to the run-scoped resolver,
    where provenance decides — the whole reason the glob was replaced."""
    from content.bio.viewers.launchers import pagoda3
    import core.config as cfg

    a = tmp_path / "work" / "ana_a" / "processed.lstar.zarr"
    b = tmp_path / "work" / "ana_b" / "processed.lstar.zarr"
    for d in (a, b):
        d.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_root", lambda pid: tmp_path)
    monkeypatch.setattr(cfg, "project_data_dir", lambda pid: tmp_path / "data")
    _locate_returning(monkeypatch, a, b)

    node = {"path": "threads/t/runs/r/output/processed.lstar.zarr",
            "name": "processed.lstar.zarr"}
    got = pagoda3._resolve_source(node, "prj_x")
    assert got not in (a, b), f"picked {got} out of two same-named outputs"


def test_rscript_resolves_for_rds_bridge(monkeypatch):
    from content.bio.viewers.launchers import pagoda3
    # honors $LSTAR_RSCRIPT when it points at a real file
    import sys
    monkeypatch.setenv("LSTAR_RSCRIPT", sys.executable)   # any existing executable
    assert pagoda3._rscript() == sys.executable


def test_convert_any_sets_lstar_rscript_env(monkeypatch, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    import sys
    seen = []

    def fake_run(args, **kw):
        seen.append((args, kw.get("env") or {}))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(pagoda3, "_rscript", lambda pid=None: sys.executable)
    pagoda3._convert_any(tmp_path / "x.rds", tmp_path / "o.building")
    env = next(e for a, e in seen if "convert" in a)      # not the basis probe
    assert env.get("LSTAR_RSCRIPT") == sys.executable     # bridge points at R


def _make_store(d):
    d.mkdir(parents=True); (d / ".zattrs").write_text("{}"); return d


def test_serve_native_store_symlinks_in_project(tmp_path):
    """A store inside the project (a run's work/ output) is SYMLINKED into the
    served dir — no multi-GB copytree on every open."""
    from content.bio.viewers.launchers import pagoda3
    root = tmp_path / "proj"
    store = _make_store(root / "work" / "ana" / "seurat.lstar.zarr")
    cache = root / "pagoda3"
    out = pagoda3._serve_native_store(store, cache, "seurat-abcd.lstar.zarr", root)
    assert out.is_symlink()                          # linked, not copied
    assert out.resolve() == store.resolve()
    assert (out / ".zattrs").read_text() == "{}"     # served in place


def test_serve_native_store_idempotent(tmp_path):
    from content.bio.viewers.launchers import pagoda3
    root = tmp_path / "proj"
    store = _make_store(root / "work" / "ana" / "s.lstar.zarr")
    cache = root / "pagoda3"
    a = pagoda3._serve_native_store(store, cache, "s-abcd.lstar.zarr", root)
    b = pagoda3._serve_native_store(store, cache, "s-abcd.lstar.zarr", root)
    assert a == b and b.is_symlink() and b.resolve() == store.resolve()


def test_serve_native_store_copies_when_outside_project(tmp_path):
    """A store OUTSIDE the project can't be reached through the project sandbox,
    so it's copied in (fallback) rather than symlinked."""
    from content.bio.viewers.launchers import pagoda3
    root = tmp_path / "proj"; root.mkdir()
    store = _make_store(tmp_path / "external" / "e.lstar.zarr")
    cache = root / "pagoda3"
    out = pagoda3._serve_native_store(store, cache, "e-abcd.lstar.zarr", root)
    assert not out.is_symlink() and out.is_dir()     # real copied tree
    assert (out / ".zattrs").read_text() == "{}"


def test_serve_native_store_replaces_stale_copy(tmp_path):
    """An old copied tree at the target is replaced by a fresh symlink (e.g. after
    this fix ships to a box whose cache still holds a pre-existing copy)."""
    from content.bio.viewers.launchers import pagoda3
    root = tmp_path / "proj"
    store = _make_store(root / "work" / "ana" / "s.lstar.zarr")
    cache = root / "pagoda3"; cache.mkdir(parents=True)
    stale = cache / "s-abcd.lstar.zarr"; stale.mkdir(); (stale / "old").write_text("x")
    out = pagoda3._serve_native_store(store, cache, "s-abcd.lstar.zarr", root)
    assert out.is_symlink() and out.resolve() == store.resolve()
    assert not (out / "old").exists()                # stale copy gone


def test_dist_path_stays_within_aba_home(monkeypatch, tmp_path):
    """The viewer dist is the module's vendored bundle under $ABA_HOME — a deployed ABA
    never reaches into other $HOME paths. Only $ABA_PAGODA3_DIST (explicit dev opt-in)
    points elsewhere."""
    from content.bio.viewers.launchers import pagoda3
    monkeypatch.delenv("ABA_PAGODA3_DIST", raising=False)
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    assert pagoda3.pagoda3_dist_path() == tmp_path / "vendor" / "pagoda3" / "dist"
    # explicit override is the ONLY outside-$ABA_HOME path
    monkeypatch.setenv("ABA_PAGODA3_DIST", "/custom/build/dist")
    from pathlib import Path
    assert pagoda3.pagoda3_dist_path() == Path("/custom/build/dist")


def test_rscript_prefers_the_ACTIVATION_SHIM_when_R_needs_it(monkeypatch, tmp_path):
    """The .rds bridge is a SECOND door onto the same rule, and it had the same
    bug in a different shape: `_rscript` only reached for a shim when
    `project_env.interpreter()` RAISED. On a directly-execable prefix it does
    not raise — it returns `<prefix>/bin/Rscript`, which cannot see the pack's
    `cran:` layer (a separate `rlib` on the activation's R_LIBS). lstar then
    execs that path and finds no lstar R package at all.

    Both doors now ask one predicate (`project_env._needs_activation`).
    """
    from content.bio.viewers.launchers import pagoda3
    from core.compute import base_env, project_env

    monkeypatch.delenv("LSTAR_RSCRIPT", raising=False)
    monkeypatch.setattr(base_env, "active", lambda lang: lang == "r")
    monkeypatch.setattr(project_env, "runtime",
                        lambda pid, lang: {"prefix": "/opt/envs/x",
                                           "direct_exec": True,
                                           "activation": "source /opt/envs/x/activate.sh"})
    # If the bug were back, this would be handed out instead of the shim.
    monkeypatch.setattr(project_env, "interpreter",
                        lambda pid, lang: Path("/opt/envs/x/bin/Rscript"))
    shim = tmp_path / "aba-rscript-shim"
    shim.write_text("#!/bin/sh\n")
    monkeypatch.setattr(pagoda3, "_rscript_shim", lambda pid: str(shim))

    got = pagoda3._rscript("prj_x")
    assert got == str(shim), (
        f"handed lstar {got!r} — a bare Rscript cannot see the pack's cran layer")


def test_rscript_uses_the_plain_interpreter_when_no_activation_exists(monkeypatch,
                                                                     tmp_path):
    """CEILING: a served-base / pack-less deploy has no activation to route
    through, and the direct interpreter is both correct and cheaper there."""
    from content.bio.viewers.launchers import pagoda3
    from core.compute import base_env, project_env

    rs = tmp_path / "Rscript"
    rs.write_text("#!/bin/sh\n")
    monkeypatch.delenv("LSTAR_RSCRIPT", raising=False)
    monkeypatch.setattr(base_env, "active", lambda lang: lang == "r")
    monkeypatch.setattr(project_env, "runtime",
                        lambda pid, lang: {"prefix": str(tmp_path),
                                           "direct_exec": True, "activation": None})
    monkeypatch.setattr(project_env, "interpreter", lambda pid, lang: rs)
    monkeypatch.setattr(pagoda3, "_rscript_shim",
                        lambda pid: (_ for _ in ()).throw(
                            AssertionError("built a shim with no activation")))
    assert pagoda3._rscript("prj_x") == str(rs)
