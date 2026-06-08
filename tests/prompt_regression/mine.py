"""Mine captured raw requests (ABA_RAW_REQUEST_DIR/req_*.json) for corpus candidates.

Indexes each request and buckets it by the GAP-AXIS decision point it represents, so we
can pick critical cases to harvest (see harvest.py). Per the corpus-growth directive, the
corpus is a representative benchmark — we want coverage of critical request types.

  python mine.py <dir> [<dir2> ...]
"""
import sys, json, glob, os, re
sys.path.insert(0, os.path.dirname(__file__))
from harness import _last_user_text

FAIL_RE = re.compile(r"\b(error|failed|failure|403|404|not found|no such|empty|traceback|exception|denied|timed out|unable)\b", re.I)
DESTR_RE = re.compile(r"\b(delete|remove|replace|overwrite|drop|discard|wipe|reset)\b", re.I)
ANALYSIS_RE = re.compile(r"\b(run|process|cluster|differential|\bDE\b|qc|normaliz|analy|integrat|trajector|annotat|enrich|seurat|scanpy|deseq|umap|pca|marker)\b", re.I)
CURATE_TOOLS = {"create_finding", "create_claim", "promote_to_result", "register_dataset", "annotate_entity"}


def _recent_blocks(msgs, k=8):
    out = []
    for m in msgs[-k:]:
        c = m["content"]
        if isinstance(c, list):
            out += [(m["role"], b) for b in c if isinstance(b, dict)]
    return out


def index_req(path):
    req = json.load(open(path))
    msgs = req.get("messages", [])
    lu = _last_user_text(msgs)
    tools, results = [], []
    for m in msgs:
        c = m["content"]
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "tool_use":
                        tools.append(b["name"])
                    elif b.get("type") == "tool_result":
                        rc = b.get("content")
                        results.append(rc if isinstance(rc, str) else json.dumps(rc))
    recent = [b["name"] for r, b in _recent_blocks(msgs) if b.get("type") == "tool_use"]
    recent_results = " ".join(results[-3:])
    interrupted = sum("interrupted" in r for r in results)
    return {
        "file": os.path.basename(path), "n": len(msgs), "last_user": lu,
        "tools": tools, "recent": recent, "interrupted": interrupted,
        "fail_ctx": bool(FAIL_RE.search(recent_results)) and "interrupted" not in recent_results[:40],
        "destructive": bool(DESTR_RE.search(lu)),
        "ambiguous": len(lu.strip()) < 45 and not ANALYSIS_RE.search(lu),
        "did_curation": bool(CURATE_TOOLS & set(tools)),
        "asks_analysis": bool(ANALYSIS_RE.search(lu)),
        "has_code": ("run_python" in tools or "run_r" in tools),
    }


def main():
    dirs = sys.argv[1:] or ["/tmp/aba_corpus_req", "/tmp/aba_raw_req"]
    idx = []
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "req_*.json"))):
            try:
                idx.append((os.path.join(d, os.path.basename(f)), index_req(f)))
            except Exception as e:  # noqa: BLE001
                print(f"skip {f}: {e}")
    print(f"indexed {len(idx)} requests from {dirs}\n")
    # dedupe distinct decision points by last_user prefix; keep richest-context exemplar
    groups = {}
    for path, r in idx:
        key = r["last_user"][:55].strip().lower()
        if key not in groups or r["n"] > groups[key][1]["n"]:
            groups[key] = (path, r)
    print(f"{len(groups)} distinct decision points (by last-user prefix)\n")

    buckets = {
        "fail_honesty (failure in recent ctx)": lambda r: r["fail_ctx"],
        "destructive_ask": lambda r: r["destructive"],
        "ambiguous_ask (clarification)": lambda r: r["ambiguous"],
        "auto_curation_ctx (curation already done in history)": lambda r: r["did_curation"],
        "analysis_request (recipe-follow / plan)": lambda r: r["asks_analysis"] and not r["did_curation"],
    }
    for label, pred in buckets.items():
        hits = [(p, r) for p, r in groups.values() if pred(r)]
        print(f"### {label}: {len(hits)}")
        for p, r in sorted(hits, key=lambda x: -x[1]["n"])[:8]:
            d = os.path.basename(os.path.dirname(p))
            print(f"   {d}/{r['file']}  n={r['n']} intr={r['interrupted']} recent={r['recent'][-4:]}")
            print(f"      last_user: {r['last_user'][:130]}")
        print()


if __name__ == "__main__":
    main()
