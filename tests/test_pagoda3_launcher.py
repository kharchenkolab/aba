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
    assert top_external("sample.lstar.zarr") == "pagoda3_launcher"
    # a non-single-cell file gets no external viewer
    assert top_external("table.csv") is None
