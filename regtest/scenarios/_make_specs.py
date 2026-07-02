#!/usr/bin/env python
"""Write each scenario's scenario.yaml (prompt + checkable expectations) + the
library README. Run from repo root after _make_data.py:
    .venv/bin/python regtest/scenarios/_make_specs.py
"""
from __future__ import annotations
import yaml
from pathlib import Path

HERE = Path(__file__).parent

SCENARIOS = {
    "bulk_de": {
        "title": "Bulk RNA-seq differential expression with a batch covariate (R/DESeq2)",
        "domain": "transcriptomics",
        "prompt": ("I have a bulk RNA-seq count table at DATA_DIR/counts.csv (genes × samples) and a "
                   "sample sheet DATA_DIR/samples.csv with a `condition` column (control vs treated) and a "
                   "`batch` column. Using DESeq2 in R, find the genes differentially expressed between "
                   "treated and control while controlling for batch, and show me the top hits."),
        "data_files": ["counts.csv", "samples.csv"],
        "expected": {
            "must_mention": ["DESeq2", "batch", "condition"],
            "must_not": [],
            "notes": ("Design ~ batch + condition. Truth: 150 planted DE genes (69 up / 81 down) on a "
                      "negative-binomial sim. Agent should read deseq2-r, install DESeq2 (conda binary), "
                      "report a sensible DE count in BOTH directions, not just up. No fabrication of counts."),
        },
    },
    "tpm": {
        "title": "Counts → TPM and top-expressed genes",
        "domain": "transcriptomics",
        "prompt": ("I have raw RNA-seq counts in DATA_DIR/counts.csv (genes × samples) and gene lengths in "
                   "DATA_DIR/lengths.csv. Convert the counts to TPM and show me the 10 genes with the "
                   "highest average expression."),
        "data_files": ["counts.csv", "lengths.csv"],
        "expected": {
            "must_mention": ["TPM", "GENE0392", "GENE0271"],
            "must_not": [],
            "notes": ("Length-normalized TPM (divide by length, then per-sample scale to 1e6). Deterministic "
                      "top-10 by mean TPM: GENE0392, GENE0271, GENE0101, GENE0098, GENE0051, GENE0488, "
                      "GENE0330, GENE0643, GENE0817, GENE0382 (top 2 must appear)."),
        },
    },
    "survival": {
        "title": "Survival association + Kaplan–Meier",
        "domain": "clinical_stats",
        "prompt": ("DATA_DIR/clinical.csv has columns time, event, and EGFR_expression for a cohort of "
                   "patients. Is higher EGFR expression associated with worse overall survival? Show me a "
                   "Kaplan–Meier plot split at the median and the statistics."),
        "data_files": ["clinical.csv"],
        "expected": {
            "must_mention": ["worse", "EGFR"],
            "must_not": [],
            "notes": ("Truth: hazard rises with EGFR → HIGH EGFR = WORSE/shorter survival; log-rank should be "
                      "significant (p<0.05). Correct direction + a KM plot + a real p-value."),
        },
    },
    "microbiome": {
        "title": "16S alpha diversity between groups",
        "domain": "microbiome",
        "prompt": ("DATA_DIR/otu.csv is a 16S feature table (taxa × samples) and DATA_DIR/meta.csv labels "
                   "each sample as 'healthy' or 'disease'. Compute alpha diversity per sample and tell me "
                   "whether it differs between the two groups."),
        "data_files": ["otu.csv", "meta.csv"],
        "expected": {
            "must_mention": ["diversity", "disease", "healthy"],
            "must_not": [],
            "notes": ("Truth: disease samples have fewer taxa + lower evenness → LOWER alpha diversity, "
                      "difference significant (p<0.05). Shannon/Simpson + a group test (Mann-Whitney sensible "
                      "for n=10/group)."),
        },
    },
    "enrichment": {
        "title": "Over-representation / pathway enrichment of a gene list",
        "domain": "functional_genomics",
        "prompt": ("DATA_DIR/genes.txt has a list of genes that came out of my screen (one per line). What "
                   "biological processes or pathways are over-represented in this list?"),
        "data_files": ["genes.txt"],
        "expected": {
            "must_mention": ["cell cycle"],
            "must_not": [],
            "notes": ("Genes are canonical cell-cycle/mitosis (CDK1, CCNB1, AURKA, PLK1, …). Enrichment should "
                      "rank cell cycle / mitosis / nuclear division at top (GO + KEGG via Enrichr/gget)."),
        },
    },
    "liftover": {
        "title": "Coordinate liftover hg19 → hg38",
        "domain": "genomics",
        "prompt": ("I have some genomic positions in the old hg19 assembly in DATA_DIR/positions.bed. Convert "
                   "them to the current hg38 coordinates."),
        "data_files": ["positions.bed"],
        "expected": {
            "must_mention": ["hg38"],
            "must_not": [],
            "notes": ("4 real hg19 intervals (APOE/EGFR/BRCA1/KRAS). Expect successful conversion, chromosome "
                      "preserved, positions shifted ~0.3–0.5 Mb (e.g. APOE chr19:45.41M → ~44.91M). Must not "
                      "fabricate — if a position is unmapped, say so."),
        },
    },
    "alphafold": {
        "title": "AlphaFold per-residue confidence (pLDDT)",
        "domain": "structural_biology",
        "prompt": ("Get the AlphaFold predicted structure for human p53 (UniProt P04637) and summarize the "
                   "per-residue confidence — which regions are confidently folded versus likely disordered?"),
        "data_files": [],
        "expected": {
            "must_mention": ["pLDDT", "disordered"],
            "must_not": [],
            "notes": ("Real pLDDT from the AlphaFold DB for P04637. p53: structured DNA-binding domain (~94–312, "
                      "high pLDDT) + disordered N-terminal TAD and C-terminal regulatory region (low pLDDT). "
                      "Real per-residue values, not invented."),
        },
    },
    "cheminformatics": {
        "title": "Drug structure + Lipinski properties",
        "domain": "cheminformatics",
        "prompt": ("For the drug imatinib, get its chemical structure (SMILES) and compute its molecular "
                   "weight, logP, and whether it satisfies Lipinski's rule of five."),
        "data_files": [],
        "expected": {
            "must_mention": ["Lipinski"],
            "must_not": [],
            "notes": ("Imatinib: SMILES from PubChem, MW ≈ 493.6, logP ≈ 3–4.5, HBD 2 / HBA ~7 → PASSES "
                      "Lipinski (≤1 violation). RDKit. MW must be ~493, not invented."),
        },
    },
    "crispr_guides": {
        "title": "CRISPR knockout guide design",
        "domain": "genome_engineering",
        "prompt": ("I want to knock out the human gene BRCA1 with CRISPR. Design a few good knockout guide "
                   "RNAs for it."),
        "data_files": [],
        "expected": {
            "must_mention": ["BRCA1"],
            "must_not": [],
            "notes": ("Valid SpCas9 candidates: 20 nt protospacer + NGG PAM, drawn from the REAL BRCA1 "
                      "sequence (Ensembl), GC 40–70%, no poly-T. Honest that off-target scoring needs a "
                      "dedicated tool. Must not fabricate guide sequences."),
        },
    },
    "blast_seq": {
        "title": "Identify an unknown protein sequence",
        "domain": "sequence_analysis",
        "prompt": ("DATA_DIR/mystery.fasta has a protein sequence I pulled from an old file with no "
                   "annotation. What protein is it and what organism is it from?"),
        "data_files": ["mystery.fasta"],
        "expected": {
            "must_mention": ["fluorescent", "Aequorea"],
            "must_not": [],
            "notes": ("The sequence is GFP (green fluorescent protein) from Aequorea victoria (UniProt P42212). "
                      "Correct answer requires a similarity search (BLAST), not a keyword/text search. Must "
                      "report the real top hit, not a guess."),
        },
    },
}

README = """# Scenario library

Real, user-like prompts + (where relevant) realistic associated data + the
**expected findings** so a run can be checked. Used to regression-test the live
agent's behaviour: discovery, recipe uptake, tool use, correctness, and the
absence of fabrication.

## Layout
```
regtest/scenarios/
  <id>/
    scenario.yaml     # title, domain, prompt, data_files, expected{must_mention, must_not, notes}
    data/             # the static input files the prompt refers to (DATA_DIR/<file>)
  _make_data.py       # regenerates the realistic data (fixed seeds → stable expectations)
  _make_specs.py      # regenerates the scenario.yaml specs + this README
```

## Running
`regtest/harness/library_runner.py` loads a scenario, stages its `data/` into the
run's DATA_DIR, drives the live agent (Haiku), streams the transcript, and prints
a coarse PASS/FAIL by checking `expected.must_mention` / `must_not` against the
final answer. Detailed per-turn context dumps land in the run's turn-log dir for
deeper analysis.

    ABA_SCENARIO=enrichment .venv/bin/python -u regtest/harness/library_runner.py

`expected.notes` is the human description of what a correct result looks like
(the auto-check only does substring matching; real judgement is in the notes).

## Conventions
- Prompts are written the way a biologist would ask — they do NOT name the tool.
- Data is realistic (negative-binomial counts, log-normal abundances, real
  coordinates, a real GFP sequence), with a known planted truth so expectations
  are concrete.
- Add a scenario: make `<id>/`, add data to `_make_data.py` (if any) + a spec to
  `_make_specs.py`, regenerate.
"""

_skipped = []
for sid, spec in SCENARIOS.items():
    sdir = HERE / sid
    sdir.mkdir(parents=True, exist_ok=True)
    target = sdir / "scenario.yaml"
    # Don't clobber a scenario that's been UPGRADED to v2 (multi-turn `steps:`).
    # Several v1 stubs here were rewritten by hand/agents into v2 realistic
    # sessions (run by regtest/harness/runner.py); regenerating the v1 spec
    # would destroy them. v2 files are self-managed, not by this generator.
    if target.exists():
        import yaml as _y
        try:
            if (_y.safe_load(target.read_text()) or {}).get("steps"):
                _skipped.append(sid); continue
        except Exception:
            pass
    doc = {"id": sid, **spec}
    target.write_text(yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True))

(HERE / "README.md").write_text(README)
print(f"wrote {len(SCENARIOS) - len(_skipped)} v1 scenario.yaml specs + README"
      + (f"; skipped {len(_skipped)} upgraded-to-v2: {_skipped}" if _skipped else ""))
