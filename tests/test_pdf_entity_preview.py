"""Phase 3 of artifact-as-truth/preview redesign.

End-to-end with the entity layer: when an artifact is a non-raster
canonical (PDF), the materialized entity carries metadata.preview_path
pointing at a sibling .preview.png that actually exists on disk. PNG
artifacts skip the preview path entirely (falls back to artifact_path
on the frontend).

Covers both materialization paths:
  - pin_artifact (Option B path: lazy materialization)
  - make_revision (creates a new revision entity from R/Py code that
    happens to emit a PDF)

Run: .venv/bin/python tests/test_pdf_entity_preview.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_pdf_entity_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "pdf.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
from core.graph import entities         # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _disk_for(url: str) -> Path:
    """Resolve an /artifacts/<pid>/<name> URL to its on-disk path."""
    from core.config import project_artifacts_dir
    parts = url[len("/artifacts/"):].split("/")
    assert len(parts) == 2, f"unexpected URL shape: {url!r}"
    return project_artifacts_dir(parts[0]) / parts[1]


def _make_pdf_via_runpython(thread_id: str = "thr_pdf") -> dict:
    """Run Python code that writes a 1-page PDF + harvests it. Returns
    the run_python result dict (with `plots`/`tables`/`files`/etc.)."""
    from content.bio.tools.run_exec import run_python
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "from matplotlib.backends.backend_pdf import PdfPages\n"
        "with PdfPages('umap.pdf') as pdf:\n"
        "    fig = plt.figure(figsize=(3,2))\n"
        "    plt.plot([1,2,3],[1,4,9])\n"
        "    pdf.savefig(fig); plt.close(fig)\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": thread_id,
                                          "tool_use_id": "tu_pdf"})
    return res


def test_pin_artifact_pdf_writes_preview_path():
    print("\n[1] pin_artifact on a PDF figure → entity.metadata.preview_path set")
    init_db()
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    res = _make_pdf_via_runpython()
    check("run_python succeeded (no error)", not res.get("error"),
          f"error={res.get('error')!r} stderr={(res.get('stderr') or '')[:120]!r}")
    check("PDF landed in `plots` bucket", len(res.get("plots") or []) == 1,
          f"plots={res.get('plots')}, files={res.get('files')}")
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": ""},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_pdf",
    )
    out = pin_artifact(res["exec_id"], "figure", 0,
                       wrap_in_result=False, thread_id="thr_pdf")
    eid = out["entity_id"]
    ent = entities.get_entity(eid)
    artifact_path = ent.get("artifact_path") or ""
    md = ent.get("metadata") or {}
    preview = md.get("preview_path")
    check("entity.artifact_path is a .pdf URL", artifact_path.endswith(".pdf"),
          f"got {artifact_path!r}")
    check("entity.metadata.preview_path is set", isinstance(preview, str) and bool(preview),
          f"got {preview!r}")
    check("preview ends with .preview.png", preview and preview.endswith(".preview.png"),
          f"got {preview!r}")
    if preview:
        thumb = _disk_for(preview)
        check("preview file exists on disk", thumb.exists(),
              f"checked {thumb}")
        if thumb.exists():
            check("preview file is non-trivially sized", thumb.stat().st_size > 100,
                  f"size={thumb.stat().st_size}")


def test_make_revision_pdf_sets_preview_path():
    print("\n[2] make_revision producing a PDF → new entity has preview_path")
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    from content.bio.lifecycle.revisions import make_revision
    # Seed: a PNG anchor (the original figure)
    from content.bio.tools.run_exec import run_python
    seed_res = run_python({"code":
        "import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
        "plt.figure();plt.plot([1,2,3],[1,4,9]);plt.savefig('seed.png');plt.close('all')"
    }, ctx={"thread_id":"thr_pdf_rev","tool_use_id":"tu_seed"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code":""},
        result_obj=seed_res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_pdf_rev",
    )
    seed = pin_artifact(seed_res["exec_id"], "figure", 0,
                        wrap_in_result=False, thread_id="thr_pdf_rev")
    anchor_id = seed["entity_id"]
    # Revise with PDF output
    rev_code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "from matplotlib.backends.backend_pdf import PdfPages\n"
        "with PdfPages('rev.pdf') as pdf:\n"
        "    fig = plt.figure(figsize=(3,2))\n"
        "    plt.plot([1,2,3],[2,5,11]); pdf.savefig(fig); plt.close(fig)\n"
    )
    out = make_revision(anchor_id, rev_code, thread_id="thr_pdf_rev")
    new_id = out["new_entity_id"]
    new_ent = entities.get_entity(new_id)
    md = new_ent.get("metadata") or {}
    check("revision entity created", isinstance(new_id, str), f"out={out}")
    check("revision artifact_path is a PDF",
          (new_ent.get("artifact_path") or "").endswith(".pdf"),
          f"got {new_ent.get('artifact_path')!r}")
    preview = md.get("preview_path")
    check("revision metadata.preview_path is set",
          isinstance(preview, str) and bool(preview), f"got {preview!r}")
    if preview:
        check("preview path is .preview.png",
              preview.endswith(".preview.png"), f"got {preview!r}")
        check("preview file exists", _disk_for(preview).exists(),
              f"checked {_disk_for(preview)}")


def test_png_artifact_has_no_preview_path():
    print("\n[3] PNG artifact → entity has NO preview_path (preview = artifact)")
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    res = run_python({"code":
        "import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt;"
        "plt.figure();plt.plot([1,2,3],[1,4,9]);plt.savefig('p.png');plt.close('all')"
    }, ctx={"thread_id":"thr_png","tool_use_id":"tu_png"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code":""},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_png",
    )
    out = pin_artifact(res["exec_id"], "figure", 0,
                       wrap_in_result=False, thread_id="thr_png")
    ent = entities.get_entity(out["entity_id"])
    md = ent.get("metadata") or {}
    check("entity.artifact_path ends in .png",
          (ent.get("artifact_path") or "").endswith(".png"),
          f"got {ent.get('artifact_path')!r}")
    check("entity.metadata.preview_path is absent (or None) for PNG",
          md.get("preview_path") in (None, "", False),
          f"got {md.get('preview_path')!r}")


def main() -> int:
    test_pin_artifact_pdf_writes_preview_path()
    test_make_revision_pdf_sets_preview_path()
    test_png_artifact_has_no_preview_path()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL PDF-ENTITY-PREVIEW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
