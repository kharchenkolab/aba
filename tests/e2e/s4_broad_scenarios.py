"""
BROAD live recon (mode B): ~10 vague, real-biologist-style prompts spanning
databases / pull-downs / repository access AND tool identification + running.
Prompts deliberately DON'T name the tool — we watch whether the agent discovers
the right database/library/CLI, installs it, and runs it (no scraping, no
fabrication). Haiku; reuses the cached /tmp/aba_discovery env.

    ABA_SCENARIO=gene_annot .venv/bin/python -u tests/e2e/s4_broad_scenarios.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSIST = Path(os.environ.get("ABA_DISC_HOME", str(Path(tempfile.gettempdir()) / "aba_discovery")))
os.environ.setdefault("ABA_ENVS_DIR", str(PERSIST / "envs"))
_run = tempfile.mkdtemp(prefix="aba_s4_")
os.environ["ABA_DB_PATH"] = str(Path(_run) / "live.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_run) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_run) / "work")
os.environ["DATA_DIR"] = str(Path(_run) / "data")
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))

SCENARIO = os.environ.get("ABA_SCENARIO", "gene_annot")

TASKS = {
    # ---------- databases / pull-downs / repository access (no tool named) ----------
    "gene_annot": (
        "I've got a handful of human genes — TP53, EGFR, BRCA1, MYC, KRAS — and I need "
        "their Ensembl gene IDs, chromosome locations, and what type of gene each one is. "
        "Can you pull that together into a table for me?"
    ),
    "protein_uniprot": (
        "What does the human TP53 protein actually do, and what functional domains does it "
        "have? Get it from the authoritative protein database, not from memory."
    ),
    "pdb_structure": (
        "I want to look at the 3D structure with PDB id 1AKE. Fetch it and tell me the "
        "experimental method, the resolution, and how many protein chains it has."
    ),
    "variant_rsid": (
        "I have a SNP — rs7412. What gene is it in, what's its consequence on the protein, "
        "and roughly how common is it across human populations?"
    ),
    "pathway_kegg": (
        "Which biological pathways is the gene EGFR involved in? I'd like a list of pathway "
        "names, pulled from a pathway database."
    ),
    "ena_runs": (
        "There's a sequencing study under BioProject PRJNA63445 I might want to reuse. Before "
        "I commit to downloading anything, just tell me what sequencing runs/samples it has "
        "and roughly how much data that is."
    ),
    # ---------- tool identification + running (vague; varied tool types) ----------
    "bulk_de": (
        "I have a gene-by-sample read count table at DATA_DIR/counts.csv — 6 samples, the "
        "first three are controls and the last three are treated. Tell me which genes are "
        "significantly changed between the two groups."
    ),
    "scrna_qc": (
        "I've got a 10x single-cell matrix in DATA_DIR/filtered/ (the usual mtx + barcodes + "
        "features files). Run standard single-cell quality control and clustering and tell me "
        "how many cell clusters there are."
    ),
    "msa_tree": (
        "I have a few protein sequences in DATA_DIR/seqs.fasta. Line them up against each "
        "other and show me how they relate to one another evolutionarily."
    ),
    "vcf_stats": (
        "I have a small variant file at DATA_DIR/variants.vcf. Keep only the high-quality "
        "variants and tell me how many pass and what the transition/transversion ratio is."
    ),
    "deseq2_r": (
        "I have a bulk RNA-seq count table at DATA_DIR/counts.csv (genes × samples) and a sample "
        "sheet DATA_DIR/samples.csv with a `condition` column (control vs treated) and a `batch` "
        "column. Using DESeq2 in R, find the genes differentially expressed between treated and "
        "control while controlling for batch, and show me the top hits."
    ),
}

RESUMES = {}  # default "Yes, go ahead."


def _stage():
    d = Path(os.environ["DATA_DIR"])
    if SCENARIO == "bulk_de":
        import numpy as np, pandas as pd
        rng = np.random.default_rng(0)
        n = 2000
        base = rng.poisson(50, size=(n, 6)).astype(float)
        de = rng.choice(n, 120, replace=False)         # 120 genes up in treated
        base[de, 3:] *= rng.uniform(4, 10, size=(120, 1))
        df = pd.DataFrame(base.round().astype(int),
                          index=[f"GENE{i:04d}" for i in range(n)],
                          columns=["ctrl1", "ctrl2", "ctrl3", "trt1", "trt2", "trt3"])
        df.index.name = "gene"
        df.to_csv(d / "counts.csv")
    elif SCENARIO == "scrna_qc":
        import numpy as np, scipy.io, scipy.sparse, gzip, shutil
        fd = d / "filtered"; fd.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(0)
        genes, cells = 600, 250
        X = scipy.sparse.csr_matrix(rng.poisson(0.4, size=(genes, cells)))  # genes x cells
        scipy.io.mmwrite(str(fd / "matrix.mtx"), X)
        with open(fd / "matrix.mtx", "rb") as f, gzip.open(fd / "matrix.mtx.gz", "wb") as g:
            shutil.copyfileobj(f, g)
        (fd / "matrix.mtx").unlink()
        with gzip.open(fd / "barcodes.tsv.gz", "wt") as f:
            for j in range(cells):
                f.write(f"CELL{j:04d}-1\n")
        with gzip.open(fd / "features.tsv.gz", "wt") as f:
            for i in range(genes):
                f.write(f"ENSG{i:08d}\tGene{i:04d}\tGene Expression\n")
    elif SCENARIO == "msa_tree":
        base = ("MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKR"
                "QTLGQHDFSAGEGLYTHMKALRPDEDRLSPLHSVYVDQWDWERVMGDGERQFSTLKSTVEAIWAGIKATEAAVSEEFG")
        import random
        random.seed(0)
        seqs = {}
        aa = "ACDEFGHIKLMNPQRSTVWY"
        for k in range(5):
            s = list(base)
            for _ in range(8 * k):                      # increasing divergence
                p = random.randrange(len(s)); s[p] = random.choice(aa)
            seqs[f"seq{k}_div{8*k}"] = "".join(s)
        with open(d / "seqs.fasta", "w") as f:
            for name, s in seqs.items():
                f.write(f">{name}\n{s}\n")
    elif SCENARIO == "deseq2_r":
        import numpy as np, pandas as pd
        rng = np.random.default_rng(1)
        n = 1500
        X = rng.poisson(50, size=(n, 8)).astype(float)       # 8 samples: 4 control, 4 treated
        de = rng.choice(n, 100, replace=False)
        X[de, 4:] *= rng.uniform(3, 8, size=(100, 1))        # 100 genes up in treated
        X *= np.array([1, 1, 1.5, 1.5, 1, 1, 1.5, 1.5])      # batch effect (not confounded w/ condition)
        df = pd.DataFrame(X.round().astype(int), index=[f"GENE{i:04d}" for i in range(n)],
                          columns=[f"s{j}" for j in range(8)])
        df.index.name = "gene"
        df.to_csv(d / "counts.csv")                          # genes × samples (R orientation)
        meta = pd.DataFrame({"condition": ["control"] * 4 + ["treated"] * 4,
                             "batch": ["A", "A", "B", "B", "A", "A", "B", "B"]},
                            index=[f"s{j}" for j in range(8)])
        meta.index.name = "sample"
        meta.to_csv(d / "samples.csv")
    elif SCENARIO == "vcf_stats":
        import random
        random.seed(0)
        lines = ["##fileformat=VCFv4.2",
                 '##FILTER=<ID=PASS,Description="All filters passed">',
                 '##FILTER=<ID=LowQual,Description="Low quality">',
                 '##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">',
                 "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO"]
        bases = "ACGT"
        for i in range(40):
            pos = 1000 + i * 137
            ref = random.choice(bases)
            alt = random.choice([b for b in bases if b != ref])
            qual = round(random.uniform(5, 90), 1)
            filt = "PASS" if qual >= 30 else "LowQual"
            lines.append(f"chr1\t{pos}\trs{1000+i}\t{ref}\t{alt}\t{qual}\t{filt}\tDP={random.randint(10,200)}")
        (d / "variants.vcf").write_text("\n".join(lines) + "\n")


def _summ(obj, n=150):
    s = obj if isinstance(obj, str) else json.dumps(obj)
    return " ".join(s.split())[:n] + ("…" if len(" ".join(s.split())) > n else "")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
        print("No ANTHROPIC_API_KEY — skipping."); return 2
    import content.bio  # noqa: F401
    import content.bio.lifecycle.registry  # noqa: F401
    from core.graph._schema import init_db
    init_db()
    _stage()
    from fastapi.testclient import TestClient
    from main import app

    task = TASKS[SCENARIO]
    print(f"=== SCENARIO: {SCENARIO} (Haiku) ===", flush=True)
    print(f"USER: {task}\n", flush=True)
    state = {"run_id": None, "buf": []}
    seen = {"tools": [], "kinds": {}}

    def flush_text():
        t = "".join(state["buf"]).strip(); state["buf"].clear()
        if t:
            print(f"🗣  {t}", flush=True)

    def consume(stream):
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            seen["kinds"][t] = seen["kinds"].get(t, 0) + 1
            if ev.get("run_id"):
                state["run_id"] = ev["run_id"]
            if t == "delta":
                state["buf"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                flush_text(); nm = ev.get("name") or ev.get("tool") or "?"
                seen["tools"].append(nm)
                print(f"🔧 {nm}  {_summ(ev.get('input') or {}, 120)}", flush=True)
            elif t == "tool_progress":
                print(f"    ⏳ {_summ(ev.get('message'), 120)}", flush=True)
            elif t == "tool_result":
                print(f"    ✓ {_summ(ev.get('result') or {}, 170)}", flush=True)
            elif t == "plan":
                flush_text(); print(f"📋 PLAN: {_summ(ev.get('plan') or ev, 200)}", flush=True)
            elif t == "entity_registered":
                e = ev.get("entity") or ev; print(f"📦 {e.get('type')}: {e.get('title')}", flush=True)
            elif t in ("approval_pending", "clarification_pending", "notice", "error", "cancelled"):
                flush_text(); print(f"[{t}] {_summ(ev, 170)}", flush=True)
        flush_text()

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"text": task}) as resp:
            consume(resp)
        for hop in range(5):
            rid = state["run_id"]
            if not rid:
                break
            try:
                turn = client.get(f"/api/turns/{rid}").json()
            except Exception:
                break
            if turn.get("state") != "awaiting_user":
                break
            reply = RESUMES.get(SCENARIO, "Yes, go ahead.")
            print(f"\n[resume {hop+1}] → {reply}\n", flush=True)
            with client.stream("POST", f"/api/turns/{rid}/resume", json={"user_text": reply}) as r2:
                consume(r2)

    print("\n=== summary ===", flush=True)
    print("tools called:", seen["tools"], flush=True)
    print("event kinds:", seen["kinds"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
