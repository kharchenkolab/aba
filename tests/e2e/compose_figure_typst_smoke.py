"""End-to-end smoke test for the compose-figure-typst recipe.

Exercises BOTH tracks (Python and R) against the live backend the same
way an agent would: open a run, ensure_capability(typst), produce a bare
figure, then call make_revision with the recipe's actual template code.
Verifies the composed PDF lands as a single revision (no duplicate
sibling from the intermediate bare panel).

This is the test the recipe needed BEFORE I shipped it — author error
in earlier draft (recipe was based on speculative API claims, not
empirical agent flow).

Run:
    .venv/bin/python tests/e2e/compose_figure_typst_smoke.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")

_TMP = Path(tempfile.mkdtemp(prefix="aba_compose_smoke_"))
os.environ["ABA_DB_PATH"]     = str(_TMP / "test.db")
os.environ["ABA_RUNTIME_DIR"] = str(_TMP)
os.environ["ARTIFACTS_DIR"]   = str(_TMP / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(_TMP / "work")
os.environ["DATA_DIR"]        = str(_TMP / "data")
for p in ("artifacts", "work", "data"):
    (_TMP / p).mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_failures: list[str] = []


def check(label: str, cond: bool, detail: str = ""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    from core.graph._schema import init_db
    import content.bio  # noqa: F401
    init_db()

    from core.graph.entities import create_entity, get_entity, list_entities
    from core.graph.edges import add_edge, edges_to
    from content.bio.tools.run_exec import run_python, run_r
    from content.bio.lifecycle.revisions import make_revision

    # Sanity: typst is importable in this venv (the same one the kernels
    # inherit). Without this, both tracks fall over with ModuleNotFoundError.
    try:
        import typst  # noqa
        print(f"  typst {typst.__version__} available")
    except ImportError:
        print("  FAIL: typst not importable — run `pip install typst` first")
        return 1

    # ══════════════════════════════════════════════════════════════
    # Python-track
    # ══════════════════════════════════════════════════════════════
    print()
    print("[python-track] produce bare figure, then compose via recipe")
    bare_py_code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(figsize=(6, 4))\n"
        "plt.plot([1, 2, 3, 4], [1, 4, 2, 3])\n"
        "plt.title('Bare data plot')\n"
        "plt.savefig('bare_py.pdf', bbox_inches='tight')\n"
        "plt.close('all')\n"
        "import os; print('bare_py.pdf', os.path.getsize('bare_py.pdf'))\n"
    )
    ctx_py = {"thread_id": "thr_compose_py"}
    bare_py_out = run_python({"code": bare_py_code}, ctx=ctx_py)
    bare_py_artifacts = bare_py_out.get("plots") or bare_py_out.get("files") or []
    check("python-track bare figure produced",
          bool(bare_py_artifacts), str(bare_py_out)[:200])
    if not bare_py_artifacts:
        return 1

    # Materialize the bare as a figure entity (mimic auto-pin).
    bare_art = bare_py_artifacts[0]
    from content.bio.lifecycle.artifacts import pin_artifact
    bare_py_id = pin_artifact(
        exec_id=bare_py_out["exec_id"], kind="figure", idx=0,
        title="Bare data plot (python-track)",
        thread_id="thr_compose_py",
    )["entity_id"]
    print(f"  bare python figure: {bare_py_id}")

    # Recipe's Python-track template: run_python via make_revision.
    compose_py_code = '''
import os, tempfile, shutil, typst
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Render bare panel to a tempfile so the harvester doesn't pick it up.
bare_pdf_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
plt.figure(figsize=(6, 4))
plt.plot([1, 2, 3, 4], [1, 4, 2, 3])
plt.title("Bare data plot")
plt.savefig(bare_pdf_tmp, bbox_inches="tight")
plt.close("all")

# Copy into wd under a stable filename so Typst's root finds it.
bare_local = "bare_panel.pdf"
shutil.copyfile(bare_pdf_tmp, bare_local)

caption_body = "Smoke-test caption — bold *Figure 1.* label, full-width wrap."
typst_src = f\'\'\'
#set page(width: 6in, height: 5in, margin: 0.3in)
#set text(font: "Liberation Sans", size: 10pt)
#set par(leading: 0.7em)
#figure(
  image("{bare_local}", width: 100%),
  caption: [*Figure 1.* {caption_body}],
  supplement: none, numbering: none,
)
\'\'\'
with open("compose.typ", "w") as f:
    f.write(typst_src)

out_pdf = "figure_composed.pdf"
typst.compile("compose.typ", out_pdf, root=os.getcwd())

# Clean up so only out_pdf is harvested.
os.remove(bare_local)
os.remove("compose.typ")
os.remove(bare_pdf_tmp)

print("composed:", os.path.getsize(out_pdf), "bytes")
'''
    try:
        rev = make_revision(bare_py_id, compose_py_code, thread_id="thr_compose_py")
        check("python-track make_revision succeeded",
              bool(rev.get("new_entity_id")), str(rev)[:200])
    except Exception as e:  # noqa: BLE001
        check("python-track make_revision succeeded", False, repr(e)[:300])
        return 1

    # Verify only ONE new figure entity created (no sibling from bare).
    new_py_id = rev["new_entity_id"]
    py_chain_entries = [e for e in list_entities(type_filter="figure")
                        if (e.get("metadata") or {}).get("thread_id") == "thr_compose_py"]
    check("python-track: exactly 2 figures (bare + composed)",
          len(py_chain_entries) == 2, f"got {len(py_chain_entries)}")
    check("python-track composed.wasRevisionOf → bare",
          any(e["rel_type"] == "wasRevisionOf" and e["source_id"] == new_py_id
              for e in edges_to(bare_py_id)),
          "no wasRevisionOf edge from composed to bare")
    new_ent = get_entity(new_py_id) or {}
    print(f"  python-track composed PDF: {new_py_id} ({(new_ent.get('artifact_path') or '')[:80]})")
    # Resolve the /artifacts/... URL to its disk path and assert it's
    # a non-trivial PDF (composed PDFs are typically >5 KB).
    from main import _artifact_url_to_path
    ap = new_ent.get("artifact_path") or ""
    disk = _artifact_url_to_path(ap) if ap else None
    sz = disk.stat().st_size if disk and disk.exists() else 0
    check("python-track composed PDF on disk",
          bool(disk) and disk.exists() and sz > 5000,
          f"url={ap!r} disk={disk!r} size={sz}")

    # ══════════════════════════════════════════════════════════════
    # R-track
    # ══════════════════════════════════════════════════════════════
    print()
    print("[r-track] produce bare R figure, then compose via recipe")
    bare_r_code = (
        'pdf("bare_r.pdf", width=6, height=4)\n'
        'plot(1:4, c(1, 4, 2, 3), type="b", main="Bare R plot")\n'
        'dev.off()\n'
        'cat("bare_r.pdf", file.info("bare_r.pdf")$size, "\\n")\n'
    )
    ctx_r = {"thread_id": "thr_compose_r"}
    bare_r_out = run_r({"code": bare_r_code}, ctx=ctx_r)
    bare_r_artifacts = bare_r_out.get("plots") or bare_r_out.get("files") or []
    check("r-track bare figure produced",
          bool(bare_r_artifacts), str(bare_r_out)[:300])
    if not bare_r_artifacts:
        return 1
    bare_r_id = pin_artifact(
        exec_id=bare_r_out["exec_id"], kind="figure", idx=0,
        title="Bare data plot (r-track)",
        thread_id="thr_compose_r",
    )["entity_id"]
    print(f"  bare r figure: {bare_r_id}")

    # Recipe's R-track template.
    compose_r_code = r'''
.py_for_typst <- function() {
  p <- Sys.getenv("ABA_PYTHON")
  if (nzchar(p) && file.exists(p)) return(p)
  stop("ABA_PYTHON env var not set; restart the kernel.")
}
.compile_typst <- function(typ_path, out_pdf, root = NULL) {
  py <- .py_for_typst()
  if (!nzchar(py)) stop("no python3 binary found")
  if (is.null(root)) {
    code <- sprintf("import typst; typst.compile(%s, %s)",
                    shQuote(typ_path), shQuote(out_pdf))
  } else {
    code <- sprintf("import typst; typst.compile(%s, %s, root=%s)",
                    shQuote(typ_path), shQuote(out_pdf), shQuote(root))
  }
  out <- system2(py, c("-c", shQuote(code)), stdout = TRUE, stderr = TRUE)
  status <- attr(out, "status")
  if (!is.null(status) && status != 0)
    stop("typst compile failed: ", paste(out, collapse = "\n"))
  invisible(out_pdf)
}

# Render bare panel to a tempfile so the harvester doesn't pick it up.
bare_tmp <- tempfile(fileext = ".pdf")
pdf(bare_tmp, width=6, height=4)
plot(1:4, c(1, 4, 2, 3), type="b", main="Bare R plot")
dev.off()

# Stage in wd for Typst.
bare_local <- "bare_panel.pdf"
file.copy(bare_tmp, bare_local, overwrite = TRUE)

caption_body <- "Smoke-test caption — bold *Figure 1.* label, full-width wrap."
typst_src <- sprintf('
#set page(width: 6in, height: 5in, margin: 0.3in)
#set text(font: "Liberation Sans", size: 10pt)
#set par(leading: 0.7em)
#figure(
  image("%s", width: 100%%),
  caption: [*Figure 1.* %s],
  supplement: none, numbering: none,
)
', bare_local, caption_body)
writeLines(typst_src, "compose.typ")

out_pdf <- "figure_composed.pdf"
.compile_typst("compose.typ", out_pdf, root = getwd())

unlink(c(bare_local, "compose.typ", bare_tmp))

cat("composed:", file.info(out_pdf)$size, "bytes\n")
'''
    try:
        rev_r = make_revision(bare_r_id, compose_r_code, thread_id="thr_compose_r")
        check("r-track make_revision succeeded",
              bool(rev_r.get("new_entity_id")), str(rev_r)[:300])
    except Exception as e:  # noqa: BLE001
        check("r-track make_revision succeeded", False, repr(e)[:400])
        return 1

    new_r_id = rev_r["new_entity_id"]
    r_chain_entries = [e for e in list_entities(type_filter="figure")
                       if (e.get("metadata") or {}).get("thread_id") == "thr_compose_r"]
    check("r-track: exactly 2 figures (bare + composed)",
          len(r_chain_entries) == 2, f"got {len(r_chain_entries)}")
    check("r-track composed.wasRevisionOf → bare",
          any(e["rel_type"] == "wasRevisionOf" and e["source_id"] == new_r_id
              for e in edges_to(bare_r_id)),
          "no wasRevisionOf edge")
    new_ent = get_entity(new_r_id) or {}
    ap = new_ent.get("artifact_path") or ""
    disk = _artifact_url_to_path(ap) if ap else None
    sz = disk.stat().st_size if disk and disk.exists() else 0
    check("r-track composed PDF on disk",
          bool(disk) and disk.exists() and sz > 5000,
          f"url={ap!r} disk={disk!r} size={sz}")
    print(f"  r-track composed PDF: {new_r_id} ({ap[:80]}, {sz} bytes)")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL COMPOSE-FIGURE-TYPST CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
