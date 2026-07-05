"""pagoda3 launcher — unified conversion (any lstar-supported source → .lstar.zarr
via the lstar CLI) + registry coverage. See misc/pagoda3_integration.md."""
import subprocess
import types

import pytest


def test_convert_any_invokes_lstar_cli_to_store(monkeypatch, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(pagoda3, "_try_viewer_prep", lambda out: None)
    src, out = tmp_path / "processed.h5ad", tmp_path / "o.lstar.zarr.building"
    pagoda3._convert_any(src, out)
    a = seen["args"]
    # sys.executable -m lstar convert <src> <out> --to store  (--to store forces
    # store output despite the .building temp suffix)
    assert a[1:4] == ["-m", "lstar", "convert"]
    assert str(src) in a and str(out) in a
    assert a[-2:] == ["--to", "store"]


def test_convert_any_raises_with_stderr_on_failure(monkeypatch, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    monkeypatch.setattr(subprocess, "run",
                        lambda args, **kw: types.SimpleNamespace(
                            returncode=2, stdout="", stderr="unsupported source"))
    monkeypatch.setattr(pagoda3, "_try_viewer_prep", lambda out: None)
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


def test_resolve_source_falls_back_to_run_work_dir(monkeypatch, tmp_path):
    """A `.lstar.zarr` store a run wrote lands physically in work/<ana_id>/<name>,
    but the file tree may hand us only the LOGICAL output path (threads/.../output/)
    with no physical file there. _resolve_source must still find the store by
    scanning the project work dirs (regression: 'pagoda3: source not found')."""
    from content.bio.viewers.launchers import pagoda3
    import core.config as cfg

    store = tmp_path / "work" / "ana_abc" / "seurat_processed.lstar.zarr"
    store.mkdir(parents=True)
    (store / ".zattrs").write_text("{}")
    monkeypatch.setattr(cfg, "project_root", lambda pid: tmp_path)
    monkeypatch.setattr(cfg, "project_data_dir", lambda pid: tmp_path / "data")

    node = {"path": "threads/t/runs/r/output/seurat_processed.lstar.zarr",
            "name": "seurat_processed.lstar.zarr"}   # logical path, nothing on disk there
    assert pagoda3._resolve_source(node, "prj_x") == store


def test_rscript_resolves_for_rds_bridge(monkeypatch):
    from content.bio.viewers.launchers import pagoda3
    # honors $LSTAR_RSCRIPT when it points at a real file
    import sys
    monkeypatch.setenv("LSTAR_RSCRIPT", sys.executable)   # any existing executable
    assert pagoda3._rscript() == sys.executable


def test_convert_any_sets_lstar_rscript_env(monkeypatch, tmp_path):
    from content.bio.viewers.launchers import pagoda3
    import sys
    seen = {}

    def fake_run(args, **kw):
        seen["env"] = kw.get("env") or {}
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(pagoda3, "_try_viewer_prep", lambda out: None)
    monkeypatch.setattr(pagoda3, "_rscript", lambda: sys.executable)
    pagoda3._convert_any(tmp_path / "x.rds", tmp_path / "o.building")
    assert seen["env"].get("LSTAR_RSCRIPT") == sys.executable   # bridge points at R
