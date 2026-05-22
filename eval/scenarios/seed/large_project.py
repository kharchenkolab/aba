"""Seed a sprawling, multi-dataset project for scenario L4-SCALE.

  python eval/scenarios/seed/large_project.py --db /tmp/eval_scale.db

Fabricates scale-at-rest: 4 datasets, ~30 figures, ~12 results, 6 findings,
3 claims, 1 manuscript — with provenance edges — so the navigation/clutter/
audit probes have a large, known graph to work against. Figures are entity
stubs (no real artifact). Deterministic.

Planted for probes:
  - one claim (the "retrieval target") has a FULL evidence chain;
  - one claim is UNSUPPORTED (no supporting findings / edges) — the audit target.
A ground-truth JSON is written to <db>.truth.json.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "eval" / "scenarios" / "data"

DATASETS = [("monocyte_stim", "monocyte_stim.csv"), ("monocyte_ref", "monocyte_ref.csv"),
            ("tcell_stim", "tcell_stim.csv"), ("bcell_baseline", "bcell_baseline.csv")]
FIG_KINDS = ["UMAP", "QC violin", "DE volcano", "module score", "heatmap",
             "PCA", "boxplot", "trend"]


def build(db_path: str) -> dict:
    os.environ["ABA_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(ROOT / "backend"))
    import db
    db.init_db()
    ce, ae, upd = db.create_entity, db.add_edge, db.update_entity

    ds_ids, figs = {}, []
    for name, csv in DATASETS:
        ds = ce(entity_type="dataset", title=name, artifact_path=str(DATA / csv))
        ds_ids[name] = ds
        an = ce(entity_type="analysis", title=f"{name} — exploratory analyses")
        ae(an, ds, "used")
        n = 8 if name.startswith("monocyte") else 6        # ~28 figures total
        for i in range(n):
            kind = FIG_KINDS[i % len(FIG_KINDS)]
            fid = ce(entity_type="figure", title=f"{name}: {kind} #{i+1}")
            ae(fid, an, "wasGeneratedBy"); ae(fid, ds, "used")
            figs.append(fid)

    # promote ~12 figures to results
    results = []
    for i in range(0, len(figs), max(1, len(figs) // 12)):
        f = figs[i]
        rid = ce(entity_type="result", title=f"Result from figure {i+1}",
                 metadata={"interpretation": "Interpreted observation.", "evidence_figure": f})
        ae(rid, f, "wasDerivedFrom")
        results.append(rid)

    # 6 findings, each grouping 1-3 results
    findings = []
    titles = ["IFN response in monocytes", "Donor D6 QC failure",
              "Response peaks at 6h", "T-cell activation signature",
              "B-cell baseline heterogeneity", "Cross-lineage stimulation overlap"]
    for k, title in enumerate(titles):
        grp = results[k * 2: k * 2 + 2] or results[:1]
        fid = ce(entity_type="finding", title=title,
                 metadata={"text": title, "summary": f"{title} — supported by {len(grp)} result(s).",
                           "evidence": grp, "supporting_results": grp,
                           "caveats": [], "maturity": "candidate" if k % 2 else "checked"})
        for e in grp:
            ae(fid, e, "supports"); ae(fid, e, "wasDerivedFrom")
        findings.append(fid)
    upd(findings[0], pinned=True)

    # 3 claims. C_target = full chain (retrieval probe). C_unsupported = no
    # supporting findings/edges (audit probe). C_ok = normal.
    def claim(title, text, fset):
        cid = ce(entity_type="claim", title=title, metadata={"text": text, "supporting_findings": fset})
        for fi in fset:
            ae(cid, fi, "supports"); ae(cid, fi, "wasDerivedFrom")
        return cid

    c_ok = claim("Stimulation induces an interferon program",
                 "Stimulation drives a coordinated ISG response.", [findings[0], findings[2]])
    c_target = claim("The IFN response is monocyte-specific and time-dependent",
                     "The interferon program is specific to monocytes and peaks at 6h.",
                     [findings[0], findings[2], findings[5]])
    # UNSUPPORTED: asserts something but links no findings (the audit target).
    c_unsupported = claim("Stimulation reduces B-cell viability",
                          "Stimulation reduces B-cell viability.", [])

    narr = ce(entity_type="narrative", title="Manuscript draft",
              metadata={"text": "We characterized the stimulation response across lineages. "
                                "The interferon program was monocyte-specific and time-dependent. "
                                "Stimulation also reduced B-cell viability."})
    ae(narr, c_target, "wasDerivedFrom"); ae(narr, c_ok, "wasDerivedFrom")

    # evidence chain for the retrieval target (resolved transitively)
    target_evidence = sorted(set(findings[i] for i in (0, 2, 5)))
    return {
        "retrieval_target_claim": c_target,
        "retrieval_target_findings": target_evidence,
        "unsupported_claim": c_unsupported,
        "counts": {"datasets": len(ds_ids), "figures": len(figs),
                   "results": len(results), "findings": len(findings), "claims": 3},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    t = build(a.db)
    Path(a.db + ".truth.json").write_text(json.dumps(t, indent=2))
    print(f"seeded large project → {a.db}")
    print("counts:", t["counts"])
    print(f"ground truth → {a.db}.truth.json")
