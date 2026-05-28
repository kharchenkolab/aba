"""
LIVE Haiku end-to-end: can a real agent ORCHESTRATE the stack? Stages real
airway counts + design (deterministic setup, no tokens), then boots the app with
Haiku and asks for a DE + pathway analysis. Observes what the agent does:
tool sequence, whether it discovers+acquires the missing tool (gseapy), produces
+ registers a volcano/table, recovers the known DEX signal, and interprets.

Non-deterministic + spends tokens (Haiku). Needs ANTHROPIC_API_KEY (loaded from
.env). Scope: provided-counts (the R-data archaeology is a separate data-access
puzzle, not an agent-orchestration test).

Run:
    .venv/bin/python tests/e2e/s2_airway_live.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_tmp = tempfile.mkdtemp(prefix="aba_live_")
_data = Path(_tmp) / "data"; _data.mkdir(parents=True, exist_ok=True)
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "live.db")   # _schema reads ABA_DB_PATH (real isolation)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "refs")
os.environ["DATA_DIR"] = str(_data)
os.environ.setdefault("ABA_ENVS_DIR", "/tmp/aba_e2e_envs")   # reuse cached R + gseapy
sys.path.insert(0, str(ROOT / "backend"))

AIRWAY_TARBALL = ("https://bioconductor.org/packages/release/data/experiment/"
                  "src/contrib/airway_1.32.0.tar.gz")
DEX_GENES = {"ENSG00000120129": "DUSP1", "ENSG00000096060": "FKBP5",
             "ENSG00000163884": "KLF15", "ENSG00000157514": "TSC22D3"}


def stage_counts() -> bool:
    """Stage real airway counts.csv + samples.tsv into DATA_DIR (cached R env)."""
    from content.bio.tools import (
        fetch_url, propose_capability_tool, ensure_capability, run_python,
    )
    f = fetch_url({"url": AIRWAY_TARBALL, "filename": "airway.tar.gz"})
    if f.get("status") != "ok":
        print("SETUP FAIL: fetch", f); return False
    tb = f["path"]; scr = Path(tb).parent
    run_python({"code": f"""
import tarfile, os
with tarfile.open({tb!r}) as t:
    m = next(x for x in t.getmembers() if x.name.endswith('airway.RData'))
    m.name='airway.RData'; t.extract(m, {str(scr)!r})
"""})
    # Catalog the conda capability (fresh DB has no entry for it) then install.
    propose_capability_tool({"name": "bioconductor-summarizedexperiment", "archetype": "cli"})
    r = ensure_capability({"name": "bioconductor-summarizedexperiment"})
    if r.get("status") != "ready":
        print("SETUP FAIL: R materialize", r); return False
    rscript = scr / "exp.R"
    run_python({"code": f"""
open({str(rscript)!r},'w').write('''
e <- new.env(); load("{scr}/airway.RData", envir=e); o <- get(ls(e)[1], envir=e)
suppressMessages(library(SummarizedExperiment))
write.csv(assay(o,"counts"), "{_data}/counts.csv")
cd <- as.data.frame(colData(o))
s <- data.frame(sample_id=rownames(cd), condition=cd$dex, donor=cd$cell)
write.table(s, "{_data}/samples.tsv", sep="\\t", row.names=FALSE, quote=FALSE)
cat("OK")
''')
"""})
    run_python({"code": f"""
import subprocess
print(subprocess.run(['Rscript', {str(rscript)!r}], capture_output=True, text=True).stdout)
""", "timeout_s": 120})
    ok = (_data / "counts.csv").exists() and (_data / "samples.tsv").exists()
    print(f"SETUP {'ok' if ok else 'FAIL'}: counts.csv + samples.tsv staged" )
    return ok


def main() -> int:
    import content.bio  # noqa: F401
    import content.bio.lifecycle.registry  # noqa: F401
    from core.graph._schema import init_db
    init_db()

    if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
        print("No ANTHROPIC_API_KEY — skipping live pass."); return 2
    print("=== staging real airway counts (no tokens) ===", flush=True)
    if not stage_counts():
        return 1

    from fastapi.testclient import TestClient
    from main import app

    request = (
        "I've put bulk RNA-seq data in the data folder: counts.csv (genes x samples, "
        "raw integer counts) and samples.tsv (sample_id, condition = trt/untrt, donor). "
        "It's the airway dexamethasone experiment. Please run differential expression "
        "of treated vs untreated controlling for donor, show a volcano plot and the top "
        "up-regulated genes, and tell me whether there's a pathway/enrichment story."
    )
    print("\n=== LIVE Haiku turn ===", flush=True)
    from collections import Counter
    tools: list[str] = []
    entities: list[str] = []
    text_parts: list[str] = []
    kinds: Counter = Counter()
    notable: list[dict] = []   # non-delta/non-token events, for diagnosis
    state = {"run_id": None, "saw_plan": False}

    def consume(stream):
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            kinds[t] += 1
            if ev.get("run_id"):
                state["run_id"] = ev["run_id"]
            if t == "tool_start":
                tools.append(ev.get("name") or ev.get("tool") or "?")
            elif t == "entity_registered":
                e = ev.get("entity") or ev
                entities.append(f"{e.get('type')}:{e.get('title')}")
            elif t == "delta":
                text_parts.append(ev.get("text") or ev.get("delta") or "")
            else:
                if t == "plan":
                    state["saw_plan"] = True
                notable.append(ev)

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"text": request}) as resp:
            consume(resp)
        # Approve plan halts and let it run; loop in case execution halts again
        # (another plan, clarification, etc.). Cap to avoid runaway.
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
            sig = turn.get("pending_user_signal") or turn.get("pending_blob", {})
            print(f"[resume {hop+1}] turn {rid} awaiting_user ({sig}); sending Go", flush=True)
            with client.stream("POST", f"/api/turns/{rid}/resume",
                               json={"user_text": "Go ahead — run the full analysis."}) as r2:
                consume(r2)

    print("\n--- EVENT TYPE HISTOGRAM ---"); print(dict(kinds))
    print("\n--- NOTABLE (non-token) EVENTS ---")
    for ev in notable:
        print(json.dumps(ev)[:400])

    final = "".join(text_parts)
    print("\n--- TOOL SEQUENCE ---"); print(" → ".join(tools) or "(none)")
    print("\n--- ENTITIES REGISTERED ---"); print("\n".join(entities) or "(none)")
    print("\n--- FINAL ANSWER (tail) ---"); print(final[-1500:] or "(none)")

    print("\n--- OBSERVATIONS ---")
    print(f"  used ensure_capability/list_capabilities: {any('capab' in t for t in tools)}")
    print(f"  ran run_python: {tools.count('run_python')}x")
    print(f"  registered a figure: {any(e.startswith('figure') for e in entities)}")
    sig = [s for g, s in DEX_GENES.items() if g in final or s in final]
    print(f"  mentioned known DEX genes in answer: {sig}")
    print("\n(Live observational run — inspect the above for where the agent succeeded/stumbled.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
