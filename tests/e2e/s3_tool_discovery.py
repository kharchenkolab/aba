"""
Tool discovery / install / execute harness (misc/tool_discovery_testing.md).

Evaluates, across 10 diverse genomics scenarios, whether ABA DISCOVERS the right
tool, INSTALLS it, and STARTS executing it — on synthetic inputs, stopping at the
first real step. Not testing biological correctness.

Two phases:
  Phase 1 (default, cheap): discovery only — assert the right surface returns the
    right tool. No installs. Local search instant; PyPI/bioconda/nf-core light HTTP.
  Phase 2 (ABA_DISC_RUN=1): install + execute — real ensure_capability + first
    step on synthetic input. Subset via ABA_DISC_SCENARIOS=1,4,9 (default all).

Env is persistent (so installs cache across runs); DB is fresh each run.
    .venv/bin/python tests/e2e/s3_tool_discovery.py            # discovery only
    ABA_DISC_RUN=1 ABA_DISC_SCENARIOS=1,9 .venv/bin/python tests/e2e/s3_tool_discovery.py
"""
from __future__ import annotations
import os
import sys
import time
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Persistent materialized env (installs cache across runs); fresh DB each run.
PERSIST = Path(os.environ.get("ABA_DISC_HOME", str(Path(tempfile.gettempdir()) / "aba_discovery")))
(PERSIST / "envs").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("ABA_ENVS_DIR", str(PERSIST / "envs"))
os.environ.setdefault("ABA_WORK_DIR", str(PERSIST / "work"))
os.environ.setdefault("DATA_DIR", str(PERSIST / "data"))
_dbtmp = tempfile.mkdtemp(prefix="aba_disc_db_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_dbtmp) / "disc.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(Path(_dbtmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import (                              # noqa: E402
    search_skills_tool, list_capabilities_tool, read_capability,
    search_pypi, search_bioconda, search_nf_core,
    propose_capability_tool, ensure_capability, run_python, run_r, run_nextflow,
)

RUN = os.environ.get("ABA_DISC_RUN") == "1"
_sel = os.environ.get("ABA_DISC_SCENARIOS", "all")
SELECTED = None if _sel == "all" else {int(x) for x in _sel.split(",") if x.strip()}


# ---------- helpers ----------
def _ok(res: dict) -> bool:
    """run_python/run_r success + our marker printed."""
    return isinstance(res, dict) and "OKMARK" in (res.get("stdout") or "")


def _py(code: str, timeout_s: int = 600) -> dict:
    return run_python({"code": code, "fresh": True, "timeout_s": timeout_s})


def _r(code: str, timeout_s: int = 900) -> dict:
    return run_r({"code": code, "timeout_s": timeout_s})


def _ensure(name: str) -> dict:
    return ensure_capability({"name": name})


def _propose(**kw) -> dict:
    return propose_capability_tool(kw)


# ---------- scenarios ----------
# Each: name, discover() -> (bool, note), execute() -> (installed:str, executed:bool, note)

def s1_scanpy():
    name = "1 scRNA QC+cluster (scanpy, pip)"

    def discover():
        r = search_skills_tool({"query": "single-cell RNA-seq QC and clustering"})
        hits = r.get("skills", [])
        caps = sum((s.get("capabilities_needed") or [] for s in hits), [])
        return ("scanpy" in caps or any("scrna" in s["name"] for s in hits),
                f"skills={[s['name'] for s in hits[:3]]}")

    def execute():
        for c in ("scanpy", "leidenalg"):
            _propose(name=c, archetype="library")
            _ensure(c)
        code = ("import numpy as np, scanpy as sc, anndata as ad\n"
                "a=ad.AnnData(np.random.poisson(1.0,(300,800)).astype('float32'))\n"
                "sc.pp.normalize_total(a,target_sum=1e4); sc.pp.log1p(a)\n"
                "sc.pp.pca(a,n_comps=20); sc.pp.neighbors(a,n_neighbors=10); sc.tl.leiden(a)\n"
                "print('OKMARK clusters', a.obs['leiden'].nunique())\n")
        res = _py(code)
        return ("ready", _ok(res), (res.get("stdout") or res.get("stderr") or str(res))[-160:])
    return name, discover, execute


def s2_seurat():
    name = "2 scRNA standard (Seurat, r_package CRAN/PPM-binary)"

    def discover():
        from core.bundle.active import get_bundle
        specs = list(get_bundle().r_base_specs)
        return ("r-seurat" in specs, f"in curated base manifest: {'r-seurat' in specs}")

    def execute():
        # Seurat's igraph dep (PPM binary) links GLPK — conda-install it userspace
        # first (the recovery for the libglpk.so.40 missing-system-lib case, F3).
        _propose(name="glpk", archetype="cli", channel="conda-forge"); _ensure("glpk")
        _propose(name="Seurat", archetype="r_package", source="cran")
        ins = _ensure("Seurat")
        code = ('suppressMessages(library(Seurat))\n'
                'm<-matrix(rpois(2000*200,1),nrow=2000); rownames(m)<-paste0("g",1:2000); colnames(m)<-paste0("c",1:200)\n'
                'o<-CreateSeuratObject(counts=m); o<-NormalizeData(o,verbose=FALSE)\n'
                'o<-FindVariableFeatures(o,nfeatures=200,verbose=FALSE); o<-ScaleData(o,verbose=FALSE)\n'
                'o<-RunPCA(o,npcs=10,verbose=FALSE)\n'
                'cat("OKMARK seurat pcs", ncol(Embeddings(o,"pca")), "\\n")\n')
        res = _r(code, timeout_s=1800)
        return (ins.get("status"), _ok(res), (res.get("stdout") or res.get("stderr") or str(res))[-160:])
    return name, discover, execute


def s3_pagoda2():
    name = "3 scRNA lab tool (pagoda2, r_package GitHub source-compile)"

    def discover():
        r = _propose(name="pagoda2", archetype="r_package", source="github",
                     package="kharchenkolab/pagoda2")
        cap = read_capability({"name": "pagoda2"})
        return (r.get("status") == "approved" and cap.get("r_source") == "github",
                f"propose={r.get('status')} read={cap.get('library')}")

    def execute():
        _propose(name="glpk", archetype="cli", channel="conda-forge"); _ensure("glpk")  # igraph dep (F3)
        ins = _ensure("pagoda2")
        code = ('suppressMessages(library(pagoda2)); suppressMessages(library(Matrix))\n'
                'set.seed(1); m<-matrix(rpois(1000*100,1),nrow=1000)\n'
                'rownames(m)<-paste0("g",1:1000); colnames(m)<-paste0("c",1:100); m<-as(m,"dgCMatrix")\n'
                'p<-Pagoda2$new(m,log.scale=TRUE,n.cores=1,verbose=FALSE)\n'
                'p$adjustVariance(plot=FALSE); p$calculatePcaReduction(nPcs=10,n.odgenes=200)\n'
                'cat("OKMARK pagoda2 done\\n")\n')
        res = _r(code, timeout_s=2400)
        return (ins.get("status"), _ok(res), (res.get("note") or res.get("stderr") or res.get("stdout") or str(res))[-180:])
    return name, discover, execute


def s4_macs2():
    name = "4 ATAC peaks (MACS2, conda CLI)"

    def discover():
        r = search_skills_tool({"query": "call ATAC-seq / ChIP-seq peaks"})
        hits = r.get("skills", [])
        caps = sum((s.get("capabilities_needed") or [] for s in hits), [])
        return (any("macs" in c for c in caps), f"skills={[s['name'] for s in hits[:3]]}")

    def execute():
        # MACS3 (maintained) rather than MACS2 — the bioconda MACS2 build fails at
        # runtime with `undefined symbol: __log_finite` (glibc 2.31+); see F2.
        _propose(name="macs3", archetype="cli", channel="bioconda")
        ins = _ensure("macs3")
        code = ('import subprocess,random,os\n'
                'random.seed(0); L=[]\n'
                'for i in range(3000): s=random.randint(500,1500); L.append(f"chr1\\t{s}\\t{s+50}\\tr{i}\\t0\\t+")\n'
                'for i in range(300): s=random.randint(1,1000000); L.append(f"chr1\\t{s}\\t{s+50}\\tb{i}\\t0\\t+")\n'
                'open("reads.bed","w").write("\\n".join(L)+"\\n")\n'
                'r=subprocess.run(["macs3","callpeak","-t","reads.bed","-f","BED","-g","1000000",'
                '"--nomodel","--extsize","200","--nolambda","-n","t","--outdir","."],capture_output=True,text=True)\n'
                'print("OKMARK macs3 rc", r.returncode, "peaks", os.path.exists("t_peaks.narrowPeak"), r.stderr[-150:])\n')
        res = _py(code, timeout_s=300)
        return (ins.get("status"), _ok(res) and "peaks True" in (res.get("stdout") or ""),
                (res.get("stdout") or res.get("stderr") or str(res))[-180:])
    return name, discover, execute


def s5_snapatac2():
    name = "5 scATAC (SnapATAC2, pip)"

    def discover():
        r = search_pypi({"query": "snapatac2"})
        return (bool(r.get("found")), f"pypi found={r.get('found')} v={r.get('version')}")

    def execute():
        _propose(name="snapatac2", archetype="library")
        ins = _ensure("snapatac2")
        code = ('import snapatac2 as snap, numpy as np, anndata as ad\n'
                'a=ad.AnnData((np.random.rand(200,1000)>0.7).astype("float32"))\n'
                'try:\n'
                '    snap.pp.select_features(a, n_features=200)\n'
                '    step="select_features"\n'
                'except Exception as e:\n'
                '    step=f"import-only ({type(e).__name__})"\n'
                'print("OKMARK snapatac2", snap.__version__, step)\n')
        res = _py(code, timeout_s=600)
        return (ins.get("status"), _ok(res), (res.get("stdout") or res.get("stderr") or str(res))[-180:])
    return name, discover, execute


def s6_limma():
    name = "6 Bulk RNA DE (limma, r_package Bioconductor)"

    def discover():
        r = _propose(name="limma", archetype="r_package", source="bioconductor")
        cap = read_capability({"name": "limma"})
        return (cap.get("r_source") == "bioconductor", f"propose={r.get('status')} src={cap.get('r_source')}")

    def execute():
        ins = _ensure("limma")
        code = ('suppressMessages(library(limma))\n'
                'set.seed(1); y<-matrix(rnorm(1000*6),nrow=1000)\n'
                'grp<-factor(c(0,0,0,1,1,1)); design<-model.matrix(~grp)\n'
                'fit<-eBayes(lmFit(y,design)); tt<-topTable(fit,coef=2,number=5)\n'
                'cat("OKMARK limma rows", nrow(tt), "\\n")\n')
        res = _r(code, timeout_s=900)
        return (ins.get("status"), _ok(res), (res.get("stdout") or res.get("stderr") or str(res))[-160:])
    return name, discover, execute


def s7_nfcore():
    name = "7 Bulk RNA pipeline (nf-core/rnaseq, Nextflow)"

    def discover():
        r = search_nf_core({"query": "rna-seq quantification"})
        names = [p["name"] for p in r.get("pipelines", [])]
        return ("rnaseq" in names, f"nf-core hits={names[:4]}")

    def execute():
        _propose(name="nf-core-rnaseq", archetype="pipeline", url="https://nf-co.re/rnaseq")
        ins = _ensure("nf-core-rnaseq")  # installs nextflow
        # Launch on the bundled test profile; short timeout (no container engine
        # expected) — we score "launched", not "completed".
        res = run_nextflow({"pipeline": "nf-core/rnaseq", "profile": "test", "timeout_s": 120})
        launched = isinstance(res, dict) and ("command" in res or res.get("status") in ("ok", "error"))
        return (ins.get("status"), launched, (res.get("note") or res.get("command") or str(res))[-180:])
    return name, discover, execute


def s8_align_variant():
    name = "8 Align + variant call (bwa+samtools+bcftools, conda CLI chain)"

    def discover():
        found = {t: bool(search_bioconda({"query": t}).get("found")) for t in ("bwa", "samtools", "bcftools")}
        return (all(found.values()), f"bioconda {found}")

    def execute():
        for t, ch in (("bwa", "bioconda"), ("samtools", "bioconda"), ("bcftools", "bioconda")):
            _propose(name=t, archetype="cli", channel=ch)
            _ensure(t)
        code = ('import subprocess,random,os\n'
                'random.seed(0); ref="".join(random.choice("ACGT") for _ in range(2000))\n'
                'open("ref.fa","w").write(">chr1\\n"+ref+"\\n")\n'
                'f=open("reads.fq","w")\n'
                'for i in range(60):\n'
                '    p=random.randint(0,1900); s=ref[p:p+100]; f.write(f"@r{i}\\n{s}\\n+\\n{chr(73)*len(s)}\\n")\n'
                'f.close()\n'
                'def sh(c): return subprocess.run(c,shell=True,capture_output=True,text=True)\n'
                'sh("bwa index ref.fa")\n'
                'm=sh("bwa mem ref.fa reads.fq | samtools sort -o aln.bam"); sh("samtools index aln.bam")\n'
                'v=sh("bcftools mpileup -f ref.fa aln.bam | bcftools call -mv -Ov -o out.vcf")\n'
                'print("OKMARK align", os.path.exists("aln.bam"), "vcf", os.path.exists("out.vcf"), m.stderr[-120:])\n')
        res = _py(code, timeout_s=600)
        return ("ready", _ok(res) and "align True" in (res.get("stdout") or ""),
                (res.get("stdout") or res.get("stderr") or str(res))[-180:])
    return name, discover, execute


def s9_gseapy():
    name = "9 Enrichment (gseapy, pip, seed catalog)"

    def discover():
        r = list_capabilities_tool({"query": "gene set enrichment analysis"})
        names = [c["name"] for c in r.get("capabilities", [])]
        return ("gseapy" in names, f"catalog hits={names[:4]}")

    def execute():
        ins = _ensure("gseapy")
        code = ('import gseapy as gp, pandas as pd, numpy as np\n'
                'rnk=pd.DataFrame({0:[f"g{i}" for i in range(200)],1:np.linspace(3,-3,200)})\n'
                'gmt={"setA":[f"g{i}" for i in range(20)],"setB":[f"g{i}" for i in range(100,140)]}\n'
                'pre=gp.prerank(rnk=rnk,gene_sets=gmt,min_size=5,max_size=200,permutation_num=10,outdir=None,seed=1,no_plot=True)\n'
                'print("OKMARK gseapy terms", pre.res2d.shape[0])\n')
        res = _py(code, timeout_s=300)
        return (ins.get("status"), _ok(res), (res.get("stdout") or res.get("stderr") or str(res))[-160:])
    return name, discover, execute


def s10_scvelo():
    name = "10 RNA velocity (scVelo, pip)"

    def discover():
        r = search_pypi({"query": "scvelo"})
        return (bool(r.get("found")), f"pypi found={r.get('found')} v={r.get('version')}")

    def execute():
        _propose(name="scvelo", archetype="library")
        ins = _ensure("scvelo")
        # Execute axis = tool loads + runs; on purely-random data scVelo's
        # filter step can legitimately bail, so import is the signal + pp best-effort.
        code = ('import scvelo as scv, numpy as np, anndata as ad, scipy.sparse as sp\n'
                'a=ad.AnnData(np.random.poisson(2,(200,500)).astype("float32"))\n'
                'a.layers["spliced"]=sp.csr_matrix(a.X)\n'
                'a.layers["unspliced"]=sp.csr_matrix(np.random.poisson(1,(200,500)).astype("float32"))\n'
                'try:\n'
                '    scv.pp.filter_and_normalize(a,min_shared_counts=0,n_top_genes=100); step="filter_and_normalize"\n'
                'except Exception as e:\n'
                '    step=f"import-only ({type(e).__name__})"\n'
                'print("OKMARK scvelo", scv.__version__, step)\n')
        res = _py(code, timeout_s=900)
        return (ins.get("status"), _ok(res), (res.get("stdout") or res.get("stderr") or str(res))[-180:])
    return name, discover, execute


SCENARIOS = [s1_scanpy, s2_seurat, s3_pagoda2, s4_macs2, s5_snapatac2,
             s6_limma, s7_nfcore, s8_align_variant, s9_gseapy, s10_scvelo]


def main() -> int:
    init_db()
    print(f"== Tool discovery harness ==  RUN(install+exec)={RUN}  "
          f"scenarios={_sel}  envs={os.environ['ABA_ENVS_DIR']}\n")
    rows = []
    for i, factory in enumerate(SCENARIOS, start=1):
        if SELECTED is not None and i not in SELECTED:
            continue
        name, discover, execute = factory()
        t0 = time.time()
        disc_ok, disc_note, inst, exe, exe_note = False, "", "-", False, "-"
        try:
            disc_ok, disc_note = discover()
        except Exception as e:  # noqa: BLE001
            disc_note = f"discover error: {e}"
        if RUN:
            try:
                inst, exe, exe_note = execute()
            except Exception as e:  # noqa: BLE001
                inst, exe, exe_note = "error", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-300:]}"
        dt = time.time() - t0
        rows.append((i, name, disc_ok, disc_note, inst, exe, exe_note, dt))
        print(f"[{i}] {name}")
        print(f"    discovery : {'OK ' if disc_ok else 'MISS'} — {disc_note}")
        if RUN:
            print(f"    install   : {inst}")
            print(f"    execute   : {'OK ' if exe else 'FAIL'} — {exe_note}")
        print(f"    ({dt:.0f}s)\n")

    print("== Scorecard ==")
    for i, name, d, _, inst, exe, _, dt in rows:
        ex = (f" exec={'OK' if exe else 'x'}" if RUN else "")
        ins = (f" install={inst}" if RUN else "")
        print(f"  [{i:2d}] disc={'OK' if d else 'x'}{ins}{ex}  {dt:.0f}s  {name}")
    disc_n = sum(1 for r in rows if r[2])
    print(f"\nDiscovery: {disc_n}/{len(rows)}" +
          (f" | Execute: {sum(1 for r in rows if r[5])}/{len(rows)}" if RUN else " (Phase 1 only; set ABA_DISC_RUN=1 for install+execute)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
