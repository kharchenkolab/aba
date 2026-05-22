"""Seed a 'submitted manuscript' project for scenario L4-REVIEW.

  python eval/scenarios/seed/submitted_manuscript.py --db /tmp/eval_review.db

Fabricates a finished-paper graph (2 datasets, 5 figures, 3 results, 3 findings,
2 claims, 1 manuscript section) with provenance edges, so the scenario can start
mid-project and exercise "extend an existing paper without breaking provenance."
Figures are entity stubs (no real artifact) — sufficient for navigation/
provenance probes.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "eval" / "scenarios" / "data"


def build(db_path: str) -> dict:
    os.environ["ABA_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(ROOT / "backend"))
    import db
    db.init_db()
    ce, ae, upd = db.create_entity, db.add_edge, db.update_entity

    ds_stim = ce(entity_type="dataset", title="monocyte_stim", artifact_path=str(DATA / "monocyte_stim.csv"))
    ds_ref = ce(entity_type="dataset", title="monocyte_ref", artifact_path=str(DATA / "monocyte_ref.csv"))
    an = ce(entity_type="analysis", title="Monocyte stim — pseudobulk DE")

    figs = {}
    for key, title in [("mod", "ISG module score · per donor"),
                       ("heat", "Top ISG expression heatmap"),
                       ("loo", "Leave-one-donor-out check"),
                       ("time", "ISG score across timepoints"),
                       ("qc", "mt_fraction by donor (QC)")]:
        fid = ce(entity_type="figure", title=title)
        ae(fid, an, "wasGeneratedBy"); ae(fid, ds_stim, "used")
        figs[key] = fid
    upd(figs["mod"], pinned=True)

    def result(title, interp, fig):
        rid = ce(entity_type="result", title=title,
                 metadata={"interpretation": interp, "evidence_figure": fig})
        ae(rid, fig, "wasDerivedFrom")
        return rid

    res_isg = result("Stim elevates the ISG module", "ISG module up in stim across 5/6 donors.", figs["mod"])
    res_time = result("Response peaks at 6h", "Module score rises 0h<2h<6h.", figs["time"])
    res_qc = result("Donor D6 fails QC", "D6 mt_fraction ~0.13, ~3x others.", figs["qc"])

    def finding(title, summary, ev, caveats, maturity="checked"):
        fid = ce(entity_type="finding", title=title,
                 metadata={"text": summary, "summary": summary, "evidence": ev,
                           "supporting_results": ev, "caveats": caveats, "maturity": maturity})
        for e in ev:
            ae(fid, e, "supports"); ae(fid, e, "wasDerivedFrom")
        return fid

    f_isg = finding("Stimulation induces an IFN-high monocyte state",
                    "Stimulation induces a coordinated ISG-high state in CD14+ monocytes (5/6 donors).",
                    [res_isg, figs["mod"], figs["heat"], figs["loo"]],
                    [{"text": "CXCL10 alone is fragile when D3 is removed", "source": "skeptic"},
                     {"text": "No protein-level validation yet", "source": "user"}])
    f_time = finding("ISG response peaks at 6h", "Module score rises monotonically to 6h.",
                     [res_time, figs["time"]], [])
    f_qc = finding("Donor D6 excluded for QC", "D6 shows elevated mt_fraction; excluded downstream.",
                   [res_qc, figs["qc"]], [])
    upd(f_isg, pinned=True)

    def claim(title, text, findings):
        cid = ce(entity_type="claim", title=title, metadata={"text": text, "supporting_findings": findings})
        for fi in findings:
            ae(cid, fi, "supports"); ae(cid, fi, "wasDerivedFrom")
        return cid

    c_isg = claim("Stimulation drives an interferon response in monocytes",
                  "In CD14+ monocytes, stimulation induces a robust, time-dependent ISG program.",
                  [f_isg, f_time])
    c_qc = claim("Donor D6 is excluded from downstream analysis",
                 "Donor D6 fails QC (elevated mitochondrial fraction) and is excluded.", [f_qc])

    narr = ce(entity_type="narrative", title="Results: monocyte IFN response",
              metadata={"text": "Stimulation induced a coordinated ISG program in CD14+ monocytes "
                                "that strengthened over the timecourse (Fig 1). The effect was robust "
                                "across 5 of 6 donors. Donor D6 was excluded for quality."})
    ae(narr, c_isg, "wasDerivedFrom")

    truth = {"target_claim_isg": c_isg, "finding_isg": f_isg,
             "review_request": "add a cell-cycle regression control for the ISG claim",
             "affected_claim": c_isg, "datasets": {"stim": ds_stim, "ref": ds_ref}}
    return truth


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    t = build(a.db)
    Path(a.db + ".truth.json").write_text(json.dumps(t, indent=2))
    sys.path.insert(0, str(ROOT / "backend"))
    import db
    n = len(db.list_entities())
    print(f"seeded submitted-manuscript project → {a.db}  ({n} entities)")
    print(f"ground truth → {a.db}.truth.json")
