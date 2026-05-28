"""
Scripted end-to-end (S2-lite): real airway bulk RNA-seq DE, exercising the full
P0-P6 stack with the agent's tool calls scripted (no model). Validates that the
pieces COMPOSE: fetch → conda demand loop (pull R) → real raw counts → pip
demand loop (gseapy) → pydeseq2 DE → artifact registration → known DEX signal.

Heavy: the first run conda-solves an R/Bioconductor stack (minutes). ENVS_DIR is
a PERSISTENT shared dir so reruns reuse it; DB/work/artifacts/refs are fresh.

Run:
    .venv/bin/python tests/e2e/s2_airway_scripted.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_tmp = tempfile.mkdtemp(prefix="aba_s2_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "s2.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "refs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
# Persistent envs so the R/Bioconductor + gseapy installs are cached across runs.
os.environ.setdefault("ABA_ENVS_DIR", "/tmp/aba_e2e_envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph.entities import list_entities               # noqa: E402
import content.bio  # noqa: E402,F401
import content.bio.lifecycle.registry as _reg               # noqa: E402
from core.data import register                               # noqa: E402
from content.bio.tools import (                              # noqa: E402
    fetch_url, propose_capability_tool, ensure_capability, run_python,
)

AIRWAY_TARBALL = ("https://bioconductor.org/packages/release/data/experiment/"
                  "src/contrib/airway_1.32.0.tar.gz")
# Canonical glucocorticoid responders (Ensembl gene IDs, airway rownames).
DEX_GENES = {
    "ENSG00000120129": "DUSP1", "ENSG00000096060": "FKBP5",
    "ENSG00000163884": "KLF15", "ENSG00000157514": "TSC22D3",
    "ENSG00000179094": "PER1",
}

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    scratch = Path(os.environ["ABA_WORK_DIR"]) / "single" / "fetch"

    print("[1/7] fetch the airway package (P4 fetch_url + UA)", flush=True)
    f = fetch_url({"url": AIRWAY_TARBALL, "filename": "airway.tar.gz"})
    check("airway tarball fetched", f.get("status") == "ok" and f.get("bytes", 0) > 1_000_000, str(f)[:160])
    tarball = f.get("path")

    print("[2/7] extract the raw-counts .RData (run_python + tarfile)", flush=True)
    ex = run_python({"code": f"""
import tarfile, os
tb = {tarball!r}
out = os.path.dirname(tb)
with tarfile.open(tb) as t:
    m = next(x for x in t.getmembers() if x.name.endswith('airway.RData'))
    m.name = 'airway.RData'; t.extract(m, out)
print('RDATA', os.path.join(out, 'airway.RData'), os.path.getsize(os.path.join(out,'airway.RData')))
"""})
    rdata = str(scratch / "airway.RData")
    check("airway.RData extracted", "RDATA" in (ex.get("stdout") or "") and Path(rdata).exists(), str(ex)[:200])

    print("[3/7] materialize R via conda demand loop (P6 propose cli + P3 conda) — SLOW", flush=True)
    propose_capability_tool({"name": "bioconductor-summarizedexperiment", "archetype": "cli"})
    r = ensure_capability({"name": "bioconductor-summarizedexperiment"})
    check("R/Bioconductor materialized (Rscript on PATH)", r.get("status") == "ready", str(r)[:200])

    print("[4/7] export real raw counts from the SummarizedExperiment (Rscript via run_python)", flush=True)
    counts_csv = str(scratch / "counts.csv"); meta_csv = str(scratch / "coldata.csv")
    rscript = scratch / "export.R"
    run_python({"code": f"""
open({str(rscript)!r}, 'w').write('''
e <- new.env(); load("{rdata}", envir=e); obj <- get(ls(e)[1], envir=e)
suppressMessages(library(SummarizedExperiment))
write.csv(assay(obj, "counts"), "{counts_csv}")
write.csv(as.data.frame(colData(obj))[, c("cell","dex")], "{meta_csv}")
cat("R_EXPORT_OK", nrow(assay(obj,"counts")), ncol(assay(obj,"counts")), "\\n")
''')
"""})
    rexp = run_python({"code": f"""
import subprocess
out = subprocess.run(['Rscript', {str(rscript)!r}], capture_output=True, text=True)
print('rc', out.returncode); print(out.stdout.strip()[-300:]); print('ERR', out.stderr.strip()[-300:])
""", "timeout_s": 200})
    check("R exported real counts", "R_EXPORT_OK" in (rexp.get("stdout") or "")
          and Path(counts_csv).exists(), str(rexp)[:300])

    print("[5/7] gseapy via pip demand loop (P1/P2')", flush=True)
    g = ensure_capability({"name": "gseapy"})
    check("gseapy materialized", g.get("status") == "ready", str(g)[:160])

    print("[6/7] pydeseq2 DE ~cell+dex + volcano + results (P5 execution)", flush=True)
    de = run_python({"code": f"""
import pandas as pd, numpy as np
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
counts = pd.read_csv({counts_csv!r}, index_col=0)          # genes x samples
meta = pd.read_csv({meta_csv!r}, index_col=0)              # samples x [cell,dex]
counts = counts.loc[counts.sum(axis=1) >= 10]              # drop near-zero genes
X = counts.T.astype(int)                                   # samples x genes
meta = meta.loc[X.index]
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
dds = DeseqDataSet(counts=X, metadata=meta, design_factors=['cell','dex'], quiet=True)
dds.deseq2()
st = DeseqStats(dds, contrast=['dex','trt','untrt'], quiet=True)
st.summary()
res = st.results_df.dropna(subset=['padj'])
res.to_csv('de_results.csv')
sig = res[(res.padj < 0.1) & (res.log2FoldChange > 1)].sort_values('padj')
print('N_SIG_UP', len(sig))
print('TOP', list(sig.head(25).index))
plt.figure(figsize=(5,4))
plt.scatter(res.log2FoldChange, -np.log10(res.padj+1e-300), s=4, alpha=.4)
plt.xlabel('log2FC (dex/untrt)'); plt.ylabel('-log10 padj'); plt.title('Airway dex DE')
plt.tight_layout(); plt.savefig('volcano.png', dpi=100)
""", "timeout_s": 200})
    out = de.get("stdout") or ""
    check("DE produced a volcano + results table",
          len(de.get("plots") or []) == 1 and len(de.get("tables") or []) >= 1, str(de)[:300])
    found = [sym for ensg, sym in DEX_GENES.items() if ensg in out]
    check("recovered the known glucocorticoid signal",
          len(found) >= 2, f"found={found} stdout_tail={out[-300:]!r}")

    print("[7/7] register artifacts as entities (P0 registration + provenance)", flush=True)
    ds_id = register(counts_csv, kind="dataset", title="airway counts",
                     scope="project", lineage={"wasDerivedFrom": []})
    recs = _reg.register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": "DE"}, result_obj=de,
        focused_entity_id=ds_id, analysis_ctx={"analysis_id": None, "turn_index": 0})
    figs = [e for e in list_entities(type_filter="figure")]
    tabs = [e for e in list_entities(type_filter="table")]
    check("volcano + results registered as entities", len(figs) >= 1 and len(tabs) >= 1,
          f"{len(figs)} figs / {len(tabs)} tables / {len(recs)} new")

    print(flush=True)
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("S2-LITE AIRWAY E2E PASSED — full P0–P6 stack end-to-end on real data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
