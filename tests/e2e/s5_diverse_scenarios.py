"""
DIVERSE live recon round 2: 10 vague, real-biologist prompts across areas not yet
exercised — single-cell integration, enrichment, survival, structure/AlphaFold,
coordinate liftover, cheminformatics, sequence ID, CRISPR design, microbiome
diversity, expression normalization. Prompts don't name the tool. Haiku.

Isolates per-turn context dumps under /tmp/aba_s5_turnlog for clean full-log
analysis. Reuses the cached /tmp/aba_discovery env.

    ABA_SCENARIO=enrichment .venv/bin/python -u tests/e2e/s5_diverse_scenarios.py
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
os.environ.setdefault("ABA_TURN_LOG_DIR", "/tmp/aba_s5_turnlog")   # isolated full-context dumps
_run = tempfile.mkdtemp(prefix="aba_s5_")
os.environ["ABA_DB_PATH"] = str(Path(_run) / "live.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_run) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_run) / "work")
os.environ["DATA_DIR"] = str(Path(_run) / "data")
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))

SCENARIO = os.environ.get("ABA_SCENARIO", "enrichment")

TASKS = {
    "sc_integration": (
        "I have two 10x single-cell samples in DATA_DIR/sampleA/ and DATA_DIR/sampleB/ (each the usual "
        "mtx + barcodes + features), collected on different days. Integrate them so batch doesn't drive "
        "the clustering, cluster the combined data, and tell me how many clusters there are and whether "
        "both samples contribute to each."
    ),
    "enrichment": (
        "DATA_DIR/genes.txt has a list of genes that came out of my screen (one per line). What biological "
        "processes or pathways are over-represented in this list?"
    ),
    "survival": (
        "DATA_DIR/clinical.csv has columns time, event, and EGFR_expression for a cohort of patients. Is "
        "higher EGFR expression associated with worse overall survival? Show me a Kaplan–Meier plot split "
        "at the median and the statistics."
    ),
    "alphafold": (
        "Get the AlphaFold predicted structure for human p53 (UniProt P04637) and summarize the per-residue "
        "confidence — which regions are confidently folded versus likely disordered?"
    ),
    "liftover": (
        "I have some genomic positions in the old hg19 assembly in DATA_DIR/positions.bed. Convert them to "
        "the current hg38 coordinates."
    ),
    "cheminformatics": (
        "For the drug imatinib, get its chemical structure (SMILES) and compute its molecular weight, logP, "
        "and whether it satisfies Lipinski's rule of five."
    ),
    "blast_seq": (
        "DATA_DIR/mystery.fasta has a protein sequence I pulled from an old file with no annotation. What "
        "protein is it and what organism is it from?"
    ),
    "crispr_guides": (
        "I want to knock out the human gene BRCA1 with CRISPR. Design a few good knockout guide RNAs for it."
    ),
    "microbiome_diversity": (
        "DATA_DIR/otu.csv is a 16S feature table (taxa × samples) and DATA_DIR/meta.csv labels each sample "
        "as 'healthy' or 'disease'. Compute alpha diversity per sample and tell me whether it differs "
        "between the two groups."
    ),
    "tpm_normalize": (
        "I have raw RNA-seq counts in DATA_DIR/counts.csv (genes × samples) and gene lengths in "
        "DATA_DIR/lengths.csv. Convert the counts to TPM and show me the 10 genes with the highest average "
        "expression."
    ),
}

RESUMES = {}


def _write_10x(dpath: Path, n_genes: int, n_cells: int, seed: int, scale: float):
    import numpy as np, scipy.io, scipy.sparse, gzip, shutil
    dpath.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    X = scipy.sparse.csr_matrix((rng.poisson(0.4 * scale, size=(n_genes, n_cells))).astype("int64"))
    scipy.io.mmwrite(str(dpath / "matrix.mtx"), X)
    with open(dpath / "matrix.mtx", "rb") as f, gzip.open(dpath / "matrix.mtx.gz", "wb") as g:
        shutil.copyfileobj(f, g)
    (dpath / "matrix.mtx").unlink()
    with gzip.open(dpath / "barcodes.tsv.gz", "wt") as f:
        for j in range(n_cells):
            f.write(f"CELL{j:04d}-1\n")
    with gzip.open(dpath / "features.tsv.gz", "wt") as f:
        for i in range(n_genes):
            f.write(f"ENSG{i:08d}\tGene{i:04d}\tGene Expression\n")


def _stage():
    import numpy as np, pandas as pd
    d = Path(os.environ["DATA_DIR"])
    if SCENARIO == "sc_integration":
        _write_10x(d / "sampleA", 400, 150, seed=1, scale=1.0)
        _write_10x(d / "sampleB", 400, 150, seed=2, scale=1.6)     # batch B shifted depth
    elif SCENARIO == "enrichment":
        genes = ["CDK1", "CCNB1", "CCNB2", "CCNA2", "CDC20", "BUB1", "BUB1B", "AURKA", "AURKB",
                 "PLK1", "MKI67", "TOP2A", "BIRC5", "CENPA", "CENPE", "CENPF", "KIF11", "KIF23",
                 "NDC80", "NUF2", "CCNE1", "CDC25C", "PTTG1", "FOXM1", "TPX2", "ESPL1", "CDK4",
                 "MCM2", "MCM5", "PCNA"]
        (d / "genes.txt").write_text("\n".join(genes) + "\n")
    elif SCENARIO == "survival":
        rng = np.random.default_rng(0)
        n = 80
        expr = rng.normal(8, 1.5, n)
        base = rng.exponential(40, n)
        time = np.clip(base * np.exp(-0.25 * (expr - 8)), 1, 120)   # high EGFR → shorter time
        event = (rng.random(n) < 0.7).astype(int)
        pd.DataFrame({"time": time.round(1), "event": event, "EGFR_expression": expr.round(2)}
                     ).to_csv(d / "clinical.csv", index=False)
    elif SCENARIO == "liftover":
        (d / "positions.bed").write_text(
            "chr1\t1000000\t1000001\trs_a\n"
            "chr7\t55086714\t55086715\tEGFR_region\n"
            "chr17\t7579472\t7579473\tTP53_region\n"
            "chrX\t100000000\t100000001\trs_b\n")
    elif SCENARIO == "blast_seq":
        # Human proinsulin (INS) — a recognizable, real sequence.
        seq = ("MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQ"
               "KRGIVEQCCTSICSLYQLENYCN")
        (d / "mystery.fasta").write_text(f">unknown_seq\n{seq}\n")
    elif SCENARIO == "microbiome_diversity":
        rng = np.random.default_rng(3)
        taxa, samples = 50, 12
        M = rng.poisson(5, size=(taxa, samples))
        M[20:, 6:] = 0                                  # disease samples (6-11): fewer taxa → lower diversity
        df = pd.DataFrame(M, index=[f"OTU{i:03d}" for i in range(taxa)],
                          columns=[f"S{j:02d}" for j in range(samples)])
        df.index.name = "taxon"
        df.to_csv(d / "otu.csv")
        pd.DataFrame({"sample": [f"S{j:02d}" for j in range(samples)],
                      "group": ["healthy"] * 6 + ["disease"] * 6}).to_csv(d / "meta.csv", index=False)
    elif SCENARIO == "tpm_normalize":
        rng = np.random.default_rng(5)
        n = 800
        cts = rng.poisson(40, size=(n, 4))
        df = pd.DataFrame(cts, index=[f"GENE{i:04d}" for i in range(n)],
                          columns=[f"s{j}" for j in range(4)])
        df.index.name = "gene"
        df.to_csv(d / "counts.csv")
        lengths = rng.integers(500, 8000, n)
        pd.DataFrame({"gene": df.index, "length": lengths}).to_csv(d / "lengths.csv", index=False)


def _summ(obj, n=160):
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
                print(f"    ⏳ {_summ(ev.get('message'), 110)}", flush=True)
            elif t == "tool_result":
                print(f"    ✓ {_summ(ev.get('result') or {}, 180)}", flush=True)
            elif t == "plan":
                flush_text(); print(f"📋 PLAN: {_summ(ev.get('plan') or ev, 200)}", flush=True)
            elif t == "entity_registered":
                e = ev.get("entity") or ev; print(f"📦 {e.get('type')}: {e.get('title')}", flush=True)
            elif t in ("approval_pending", "clarification_pending", "notice", "error", "cancelled"):
                flush_text(); print(f"[{t}] {_summ(ev, 180)}", flush=True)
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
