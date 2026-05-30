"""
scRNA-seq recipe stress-suite. Runs ONE realistic scenario through the live agent
(Haiku, the default model) against real PBMC 10x data, in a PERSISTENT project so
heavy installs (pagoda2/Seurat/conos/harmony + the conda R runtime) and the staged
data cache across invocations. Each run uses its own thread. Writes a full
transcript + auto-checks must_mention.

Isolated from the running backend: dedicated /tmp/aba_scrna_suite for DB/ENVS/DATA
(DB-safe — never touches the live project's SQLite).

    .venv/bin/python -u tests/e2e/run_scrna_suite.py <scenario_name>
    scenarios: scanpy_single pagoda2_single seurat_single
               conos_integration harmony_integration seurat_integration
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# SUITE_BASE lets parallel workers run on fully-isolated trees (own DB / DATA_DIR
# / artifacts / ENVS) so a per-scenario DATA_DIR wipe in one worker can't yank
# files out from under another. Defaults to the shared tree for sequential runs.
BASE = Path(os.environ.get("SUITE_BASE", "/tmp/aba_scrna_suite"))
# Honor a pre-set ABA_ENVS_DIR so parallel eval arms can SHARE one warm envs
# (no per-arm cold reinstall of scanpy/R/snapatac2, and — since nothing needs
# installing — no concurrent-install race). Else default to per-base envs.
ENVS = Path(os.environ.get("ABA_ENVS_DIR") or (BASE / "envs"))
os.environ["ABA_ENVS_DIR"] = str(ENVS)            # persistent → installs cache
# Per-scenario DB so one scenario's entities/memories never leak into another
# (a shared DB let scanpy_single fabricate a "vs pagoda2" comparison from a prior
# run's memory). Installs (ENVS) + staged data (DATA_DIR) stay cached. Fresh by
# default for clean regression; SUITE_FRESH=0 to reuse a scenario's project.
_NAME = sys.argv[1] if len(sys.argv) > 1 else "scanpy_single"
_DB = BASE / f"db_{_NAME}.sqlite"
os.environ["ABA_DB_PATH"] = str(_DB)
if os.environ.get("SUITE_FRESH", "1") == "1":
    for _ext in ("", "-wal", "-shm"):
        try:
            Path(str(_DB) + _ext).unlink()
        except FileNotFoundError:
            pass
os.environ["ARTIFACTS_DIR"] = str(BASE / "artifacts")
os.environ["ABA_WORK_DIR"] = str(BASE / "work")
os.environ["DATA_DIR"] = str(BASE / "data")
os.environ.setdefault("ABA_TURN_LOG_DIR", str(BASE / "turnlog"))
for p in (ENVS, BASE / "artifacts", BASE / "work", BASE / "data",
          BASE / "transcripts", BASE / "turnlog"):
    p.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))

# Wipe the TEST project's typed-memory dir on a fresh run. The per-scenario DB is
# fresh, but typed memories live in projects/single/memory/ (NOT in the DB), so
# cross-session notes — incl. ones about THIS exact sample with prior cell-type
# annotations + a pagoda2 comparison — leak into a "fresh" run and PRIME scope-
# creep / annotation / fabrication (found by reading the full turn-context dump,
# not the summarized transcript). A clean regression must start with no memory.
# Only touches the isolated test pid 'single' — never a live project (DB-safe).
if os.environ.get("SUITE_FRESH", "1") == "1":
    try:
        import shutil as _shutil
        from core.projects import PROJECTS_DIR as _PD
        _memdir = _PD / "single" / "memory"
        if _memdir.is_dir():
            _shutil.rmtree(_memdir, ignore_errors=True)
    except Exception:
        pass

# Clean DATA_DIR to ONLY the staged inputs. A prior run's OUTPUT left here (e.g.
# adata_harmony_integrated.h5ad) let the agent SHORTCUT the pipeline — load the
# cached result instead of computing it (a false PASS for the integration scenarios).
if os.environ.get("SUITE_FRESH", "1") == "1":
    import shutil
    _dd = Path(os.environ["DATA_DIR"])
    for _p in _dd.iterdir():
        try:
            if _p.is_symlink() or _p.is_file():
                _p.unlink()
            else:
                shutil.rmtree(_p, ignore_errors=True)
        except OSError:
            pass

# Stage the GSM 10x triplets (symlink real files into DATA_DIR).
SRC = ROOT / "backend" / "data" / "GSE192391_controls"
for gsm in ("GSM5746268", "GSM5746269", "GSM5746270"):
    d = SRC / gsm
    if d.is_dir():
        for f in d.glob("*.gz"):
            dst = Path(os.environ["DATA_DIR"]) / f.name
            if not dst.exists():
                try:
                    os.symlink(f, dst)
                except OSError:
                    pass

if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
    print("No ANTHROPIC_API_KEY — skipping."); sys.exit(2)

P1 = "GSM5746268_MGI0369_1_SLAB-C1-C1"
P2 = "GSM5746269_MGI0369_1_SLAB-C2-C2"
P3 = "GSM5746270_MGI0369_1_SLAB-C4-C4"

SCENARIOS = {
    "scanpy_single": {
        "title": "scanpy PBMC processing",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed "
                   f"{P1} (matrix.mtx.gz, barcodes.tsv.gz, features.tsv.gz). Using scanpy, do standard "
                   f"QC, normalization, highly-variable genes, PCA, neighbors, Leiden clustering and a "
                   f"UMAP, then identify marker genes per cluster and show me a UMAP and a marker dotplot."),
        "must_mention": ["leiden", "umap", "marker"],
    },
    "pagoda2_single": {
        "title": "pagoda2 PBMC processing",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed {P1}. "
                   f"Run a standard pagoda2 workflow in R: read the counts, QC, build the Pagoda2 object, "
                   f"variance normalization, PCA, kNN graph, Leiden clustering, a UMAP embedding, and marker "
                   f"genes per cluster with a marker heatmap."),
        "must_mention": ["pagoda2", "cluster", "umap"],
    },
    "seurat_single": {
        "title": "Seurat PBMC processing",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed {P1}. "
                   f"Run a standard Seurat workflow in R: load, QC, normalize, PCA, neighbors, clustering, "
                   f"UMAP, and FindAllMarkers; show me a UMAP and the top markers per cluster."),
        "must_mention": ["seurat", "cluster", "umap"],
    },
    "conos_integration": {
        "title": "conos integration of 3 PBMC samples",
        "prompt": (f"In DATA_DIR there are 10x triplets for THREE PBMC control samples — file prefixes {P1}, "
                   f"{P2}, {P3}. Process each with pagoda2, then integrate them with conos: build the joint "
                   f"graph, joint clustering, and a joint embedding colored by sample and by cluster."),
        "must_mention": ["conos", "integrat", "cluster"],
    },
    "harmony_integration": {
        "title": "harmony integration of 3 PBMC samples",
        "prompt": (f"In DATA_DIR there are 10x triplets for THREE PBMC control samples — file prefixes {P1}, "
                   f"{P2}, {P3}. Integrate them with Harmony (scanpy + harmonypy, or R/Seurat) and show a "
                   f"UMAP before vs after integration, colored by sample."),
        "must_mention": ["harmony", "integrat", "umap"],
    },
    "seurat_integration": {
        "title": "Seurat integration of 3 PBMC samples",
        "prompt": (f"In DATA_DIR there are 10x triplets for THREE PBMC control samples — file prefixes {P1}, "
                   f"{P2}, {P3}. Integrate them with Seurat's anchor-based integration and show an integrated "
                   f"UMAP colored by sample and by cluster."),
        "must_mention": ["seurat", "integrat", "umap"],
    },
    "scvi_single": {
        "title": "scVI latent + clustering, one PBMC sample",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed {P1} "
                   f"(matrix.mtx.gz, barcodes.tsv.gz, features.tsv.gz). Using scvi-tools, do QC, set up the "
                   f"AnnData, train an scVI model on this sample (use the GPU if available), get the latent "
                   f"representation, then cluster on the scVI latent space and show a UMAP coloured by cluster "
                   f"plus marker genes per cluster."),
        "must_mention": ["scvi", "latent", "umap"],
    },
    "scvi_integration": {
        "title": "scVI integration of 3 PBMC samples",
        "prompt": (f"In DATA_DIR there are 10x triplets for THREE PBMC control samples — file prefixes {P1}, "
                   f"{P2}, {P3}. Integrate them with scvi-tools: build a combined AnnData with a sample/batch "
                   f"key, train an scVI model with the batch covariate (use the GPU if available), get the latent "
                   f"representation, cluster, and show a UMAP of the scVI latent coloured by sample and by cluster."),
        "must_mention": ["scvi", "integrat", "umap"],
    },

    # ── Diverse genomic scenarios (self-contained — no scRNA data needed) ──
    "reactome_pathways": {
        "title": "Reactome pathway enrichment for a gene list",
        "prompt": ("Here is a list of human genes from a DNA-damage / p53 signature: TP53, MDM2, "
                   "CDKN1A, BAX, GADD45A, ATM, CHEK2, BBC3, SESN1, RRM2B. Find which Reactome pathways "
                   "are over-represented / enriched in this gene set, and show me the top pathways "
                   "(a ranked table, and a barplot if easy)."),
        "must_mention": ["reactome", "pathway"],
        "needs": "figs_or_tables",
    },
    "protein_phylogeny": {
        "title": "Protein phylogeny of TP53 orthologs",
        "prompt": ("Build a phylogenetic tree of the TP53 protein across a few species — human, mouse, "
                   "rat, zebrafish, and chicken. Fetch the ortholog protein sequences, do a multiple "
                   "sequence alignment, infer the tree, and show me the tree."),
        "must_mention": ["phylo", "align", "tree"],
        "needs": "figs",
    },
    "bulk_deseq2": {
        "title": "Bulk RNA-seq differential expression (DESeq2 / pydeseq2)",
        "prompt": ("Run a bulk RNA-seq differential-expression analysis. Use pydeseq2's built-in example "
                   "dataset (or a small public bulk RNA-seq count matrix you fetch) with a two-group "
                   "design (condition A vs B). Fit the model, get the DE results table, and show me a "
                   "volcano plot and the top differentially-expressed genes."),
        "must_mention": ["deseq", "differential", "volcano"],
        "needs": "figs_or_tables",
    },
    "crispr_guides": {
        "title": "Design CRISPR-Cas9 guide RNAs for a human gene",
        "prompt": ("Design CRISPR-Cas9 (SpCas9, NGG PAM) guide RNAs targeting an early coding exon of "
                   "the human TP53 gene. Fetch the relevant sequence, find candidate 20-nt protospacers "
                   "with their PAMs and positions, and rank/show me the top candidate guides as a table."),
        "must_mention": ["guide", "pam", "crispr"],
        "needs": "figs_or_tables",
    },
    "gsea_enrichment": {
        "title": "Gene-set enrichment / over-representation (gseapy)",
        "prompt": ("Here is a set of upregulated human genes: STAT1, IRF1, GBP1, OAS1, MX1, ISG15, IFIT1, "
                   "IFIT3, OASL, RSAD2, CXCL10, DDX58. Run a gene-set over-representation / enrichment "
                   "analysis (e.g. against Hallmark or GO via Enrichr/gseapy) and show me the top enriched "
                   "terms as a ranked table and a barplot."),
        "must_mention": ["enrich", "gene set"],
        "needs": "figs_or_tables",
    },
    "scanpy_annotate": {
        "title": "scanpy clustering + cell-type annotation",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed {P1}. "
                   f"Process it with scanpy through Leiden clustering, then ANNOTATE the clusters with likely "
                   f"PBMC cell types using canonical marker genes (T cells, B cells, NK, monocytes, etc.). "
                   f"Show me a UMAP coloured by cell type and a marker dotplot."),
        "must_mention": ["cell type", "marker", "umap"],
        "needs": "figs",
    },
    "variant_vep": {
        "title": "Variant functional annotation (Ensembl VEP)",
        "prompt": ("Annotate the functional consequences of these human variants: rs1042522, rs28934578, "
                   "rs121912651, rs104894228. For each, get the gene, the consequence type, and the predicted "
                   "impact (e.g. via Ensembl VEP), and show me a summary table."),
        "must_mention": ["consequence", "variant"],
        "needs": "tables",
    },
    "blast_identify": {
        "title": "Identify a protein sequence by BLAST",
        "prompt": ("I have this protein sequence and don't know what it is:\n"
                   "MALWMRLLPLLALLALWGPDPAAAFVNQHLCGSHLVEALYLVCGERGFFYTPKTRREAEDLQVGQVELGGGPGAGSLQPLALEGSLQKRGIVEQCCTSICSLYQLENYCN\n"
                   "Identify the protein and its source organism by BLAST against a protein database, and show "
                   "me the top hits (identity, organism, description) as a table."),
        "must_mention": ["blast", "identity"],
        "needs": "tables",
    },
    "rna_velocity": {
        "title": "RNA velocity with scVelo (built-in pancreas data)",
        "prompt": ("Run an RNA velocity analysis with scVelo on its built-in pancreas dataset "
                   "(scvelo.datasets.pancreas()). Compute moments, estimate velocities (stochastic or "
                   "dynamical), build the velocity graph, and show me the velocity stream embedding on the "
                   "UMAP coloured by cell type."),
        "must_mention": ["velocity", "scvelo"],
        "needs": "figs",
    },
    "trajectory_paga": {
        "title": "Trajectory inference (PAGA + DPT) on paul15",
        "prompt": ("Using scanpy's built-in paul15 hematopoiesis dataset (sc.datasets.paul15()), infer a "
                   "developmental trajectory: build the neighbor graph, run PAGA, and diffusion pseudotime "
                   "(DPT). Show me the PAGA graph and a UMAP/embedding coloured by pseudotime."),
        "must_mention": ["paga", "pseudotime"],
        "needs": "figs",
    },
    "scrna_cluster_de": {
        "title": "Differential expression between scRNA clusters (method-choice probe)",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed {P1}. "
                   f"Cluster the cells, then run a proper DIFFERENTIAL EXPRESSION test to find genes that "
                   f"significantly distinguish one cluster from another (e.g. a T-cell cluster vs a monocyte "
                   f"cluster). Report the top significant genes with statistics."),
        "must_mention": ["differential", "cluster"],
        "needs": "figs_or_tables",
    },
    "geo_fetch_qc": {
        "title": "Fetch a GEO sample's matrix + QC (real network fetch)",
        "prompt": ("Fetch the processed 10x count matrix for the GEO sample GSM5746271 (from series "
                   "GSE192391), load it into AnnData, and run basic scanpy QC — compute QC metrics, apply "
                   "sensible filters, and show me a QC violin plot. Just the QC, nothing downstream."),
        "must_mention": ["qc", "geo"],
        "needs": "figs",
    },
    "citeseq_multimodal": {
        "title": "CITE-seq multimodal (RNA + ADT) analysis with muon",
        "prompt": ("Analyse a CITE-seq dataset (paired RNA + ADT/protein). Use a small public CITE-seq "
                   "dataset (e.g. 10x's 5k/10k PBMC CITE-seq, or a muon/scanpy built-in if available). Load "
                   "BOTH modalities (muon/MuData), QC + normalize each (CLR for the ADT, standard for RNA), "
                   "cluster, and show a UMAP coloured by cluster with a few key ADT markers overlaid."),
        "must_mention": ["adt", "cluster"],
        "needs": "figs",
    },
    "protein_domains": {
        "title": "Protein domain architecture (UniProt/InterPro features)",
        "prompt": ("For the human BRCA1 protein, retrieve its domain / functional-region architecture "
                   "(e.g. the RING, BRCT domains and other annotated features from UniProt/InterPro) and "
                   "summarise the key functional regions with their positions as a table."),
        "must_mention": ["domain", "brca1"],
        "needs": "tables",
    },
    "de_wrong_method": {
        "title": "Method-appropriateness probe — DESeq2 requested for per-cell scRNA DE",
        "prompt": (f"In DATA_DIR there is a 10x matrix triplet for one PBMC sample — files prefixed {P1}. "
                   f"Cluster the cells, then find the genes differentially expressed between the T-cell "
                   f"cluster and the monocyte cluster. Please use DESeq2 for the differential expression."),
        "must_mention": ["differential"],
        "needs": "figs_or_tables",
    },
    "scatac": {
        "title": "scATAC-seq processing (novel modality; also tests anti-fabrication on fetch-fail)",
        "prompt": ("Analyse a single-cell ATAC-seq dataset. Use a small PUBLIC scATAC-seq dataset (e.g. "
                   "10x's PBMC scATAC, or a snapATAC2/episcanpy built-in). Do basic QC (TSS enrichment, "
                   "fragment/feature counts), build the cell × peak (or bin) matrix, run TF-IDF/LSI, cluster, "
                   "and show a UMAP coloured by cluster."),
        "must_mention": ["atac", "cluster"],
        "needs": "figs",
    },
}


def _summ(obj, n=200):
    s = obj if isinstance(obj, str) else json.dumps(obj)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "scanpy_single"
    spec = SCENARIOS[name]
    import content.bio  # noqa: F401
    import content.bio.lifecycle.registry  # noqa: F401
    from core.graph._schema import init_db
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    tpath = BASE / "transcripts" / f"{name}.log"
    out = open(tpath, "w")

    def log(s=""):
        print(s, flush=True)
        out.write(s + "\n"); out.flush()

    # Log the ACTUAL model the Guide runs on — i.e. the guide spec's model
    # (bio/advisors/guide.yaml), NOT config.MODEL. config.MODEL / ABA_MODEL do
    # NOT drive the Guide (it is spec-driven); printing config.MODEL here once
    # mislabeled a whole Haiku run as "Sonnet". Fall back to config.MODEL only
    # if no guide spec resolves.
    try:
        from core.runtime.agent import get_agent_spec
        _gspec = get_agent_spec("guide")
        _MODEL = (_gspec.model if _gspec else None)
        if not _MODEL:
            from core.config import MODEL as _MODEL
    except Exception:
        _MODEL = "?"
    log(f"=== SCENARIO: {name} ({spec['title']}) — guide model: {_MODEL} ===")
    log(f"USER: {spec['prompt']}\n")
    state = {"run_id": None, "run_ids": [], "buf": [], "all_text": []}
    seen = {"tools": [], "figs": 0, "tables": 0}

    def flush_text():
        t = "".join(state["buf"]).strip(); state["buf"].clear()
        if t:
            state["all_text"].append(t); log(f"🗣  {t}")

    def consume(stream):
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            if ev.get("run_id"):
                state["run_id"] = ev["run_id"]
                if ev["run_id"] not in state["run_ids"]:
                    state["run_ids"].append(ev["run_id"])   # all turns (main + nudges)
            if t == "delta":
                state["buf"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                flush_text(); nm = ev.get("name") or ev.get("tool") or "?"
                seen["tools"].append(nm)
                inp = ev.get("input") or {}
                # The code the agent actually wrote is the #1 diagnostic — log it in
                # FULL (not truncated); summarize any other inputs alongside.
                code = inp.get("code") if isinstance(inp, dict) else None
                if code:
                    rest = {k: v for k, v in inp.items() if k != "code"}
                    log(f"🔧 {nm}  {_summ(rest, 200) if rest else ''}")
                    log("    ┌─ code ─────────────────────────────")
                    for ln in str(code).splitlines():
                        log(f"    │ {ln}")
                    log("    └────────────────────────────────────")
                else:
                    log(f"🔧 {nm}  {_summ(inp, 240)}")
            elif t == "tool_progress":
                log(f"    ⏳ {_summ(ev.get('message'), 150)}")
            elif t == "tool_result":
                res = ev.get("result") or {}
                log(f"    ✓ {_summ(res, 900)}")
                # Surface the high-signal failure fields IN FULL (the 900-char
                # summary above often clips them): stderr tracebacks + blank-figure
                # warnings are exactly what this suite exists to catch.
                if isinstance(res, dict):
                    if res.get("figure_warnings"):
                        for w in res["figure_warnings"]:
                            log(f"    ⚠ FIGURE: {w}")
                    rc = res.get("returncode")
                    err = res.get("stderr") or res.get("error")
                    if (rc not in (0, None)) and err:
                        log(f"    ✗ rc={rc} stderr: {_summ(err, 1200)}")
            elif t == "plan":
                flush_text(); log(f"📋 PLAN: {_summ(ev.get('plan') or ev, 240)}")
            elif t == "entity_registered":
                e = ev.get("entity") or ev
                if e.get("type") in ("figure", "view"):
                    seen["figs"] += 1
                elif e.get("type") in ("table", "result"):
                    seen["tables"] += 1
                log(f"📦 {e.get('type')}: {e.get('title')}")
            elif t in ("approval_pending", "clarification_pending", "notice", "error", "cancelled"):
                flush_text(); log(f"[{t}] {_summ(ev, 220)}")
        flush_text()

    t0 = time.time()
    with TestClient(app) as client:
        # Own thread per scenario.
        tid = client.post("/api/threads", json={"title": spec["title"], "question": spec["prompt"][:80]}).json().get("id", "default")
        log(f"[thread {tid}]\n")
        with client.stream("POST", "/api/chat", json={"text": spec["prompt"], "thread_id": tid}) as resp:
            consume(resp)
        nudges = 0
        for hop in range(10):
            rid = state["run_id"]
            if not rid:
                break
            try:
                turn = client.get(f"/api/turns/{rid}").json()
            except Exception:
                turn = {}
            if turn.get("state") == "awaiting_user":
                log("\n[resume] → Yes, go ahead.\n")
                with client.stream("POST", f"/api/turns/{rid}/resume", json={"user_text": "Yes, go ahead."}) as r2:
                    consume(r2)
                continue
            # Turn ended without awaiting input. If NOTHING was produced yet, the
            # agent likely stalled after announcing intent (a Haiku "narrate-then-
            # stop" — seen ~2/3 of seurat_integration runs after the initial
            # inspect). Nudge it to continue; bounded so we never loop forever.
            if seen["figs"] == 0 and seen["tables"] == 0 and nudges < 2:
                nudges += 1
                log(f"\n[continue-nudge {nudges}] → please continue.\n")
                # Neutral nudge: push past a "narrate-then-stop" WITHOUT encouraging
                # scope expansion (a pushy "run the full analysis" once sent the
                # blast_identify agent ballooning into an unrequested 8-figure study).
                with client.stream("POST", "/api/chat",
                                   json={"text": "Please continue with exactly what was requested, then stop.",
                                         "thread_id": tid}) as r3:
                    consume(r3)
                continue
            break

    blob = " ".join(state["all_text"]).lower()
    miss = [m for m in spec["must_mention"] if m.lower() not in blob]
    # A genuine completion must PRODUCE an artifact, not just TALK about it (a
    # text-only "I'll show a UMAP…" otherwise passes falsely). The artifact a
    # scenario must produce depends on its kind — `needs`: 'figs' (default,
    # figures>0), 'tables' (tables/results>0), 'figs_or_tables', or 'text' (no
    # artifact required, must_mention only).
    needs = spec.get("needs", "figs")
    have = {"figs": seen["figs"] > 0, "tables": seen["tables"] > 0,
            "figs_or_tables": seen["figs"] > 0 or seen["tables"] > 0, "text": True}[needs]
    ok = (not miss) and have
    log("\n=== summary ===")
    log(f"elapsed: {time.time()-t0:.0f}s   figures: {seen['figs']}   tables: {seen['tables']}   tools: {seen['tools']}")
    log(f"AUTOCHECK [{'PASS' if ok else 'CHECK'}]  missing={miss}  figs={seen['figs']} tables={seen['tables']} needs={needs}")
    out.close()
    # Sidecar for the auto-scorer: the exact run_ids (→ turnlog/<id>.jsonl) this
    # scenario produced, so scoring is deterministic (no thread→dump→id guessing).
    try:
        (BASE / "transcripts" / f"{name}.meta.json").write_text(json.dumps({
            "scenario": name, "model": _MODEL, "run_ids": state["run_ids"],
            "turnlog": str(BASE / "turnlog"), "arm": os.environ.get("ABA_PROMPT_ARM", "control"),
        }))
    except Exception:
        pass
    print(f"\n[transcript] {tpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
