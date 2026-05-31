"""d9 — tool-lifecycle hook registry.

Verifies the #305 veto + post-guardrails were migrated into core.runtime.hooks
LOSSLESSLY: same blocks, same order, same kill-switch, same judgment warnings as
the pre-migration snapshot (captured 2026-05-30). Run: python tests/d9_tool_hooks.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import content.bio.tools as T   # importing registers the hooks
from core.runtime import hooks

PASS, FAIL = [], []
def check(name, got, exp):
    (PASS if got == exp else FAIL).append(name)
    print(f"  {'ok  ' if got == exp else 'FAIL'} {name}: got={got!r} exp={exp!r}")

CODE = {
 "pseudorep_r":  ("run_r", "library(DESeq2)\ndds <- DESeqDataSetFromMatrix(countData=counts, colData=metadata, design=~cell_type)\ndds<-DESeq(dds)"),
 "pseudorep_py": ("run_python", "from pydeseq2.dds import DeseqDataSet\ndds=DeseqDataSet(counts=counts, metadata=obs, design='~leiden')"),
 "pseudobulk_ok":("run_python", "pb=adata.to_df().groupby(adata.obs['sample_id']).sum()\ndds=DeseqDataSet(counts=pb, metadata=meta, design='~condition')"),
 "wilcoxon_ok":  ("run_python", "sc.tl.rank_genes_groups(adata,'leiden',method='wilcoxon')"),
 "synth":        ("run_python", "import numpy as np\ncounts=np.random.poisson(2,size=(1000,500))\nadata=ad.AnnData(counts)"),
 "plain":        ("run_python", "import scanpy as sc\nadata=sc.read_10x_mtx('data')"),
 "nontool":      ("list_data_files", ""),
}
def pre(key, thread=None):
    n, c = CODE[key]; ctx = {"thread_id": thread} if thread else {}
    d, _ = hooks.run_pre(n, {"code": c}, ctx)
    return d.reason_code if isinstance(d, hooks.Deny) else None
def post_warns(key):
    n, c = CODE[key]; res = {"returncode": 0, "stdout": "", "stderr": ""}
    hooks.run_post(n, {"code": c}, res, {})
    return len(res.get("guardrail_warnings", []))

print("registered:", hooks.registered())
check("registered.pre", hooks.registered()["pre"], ["preexec_veto"])
check("registered.post", hooks.registered()["post"], ["recipe_uptake_hint", "fetch_fail_guardrail", "fetch_tool_failure_steer", "judgment_guardrails", "synth_taint_steer"])

print("=== PreToolUse veto (no thread armed) ===")
check("pseudorep_r", pre("pseudorep_r"), "PSEUDOREPLICATION_DE")
check("pseudorep_py", pre("pseudorep_py"), "PSEUDOREPLICATION_DE")
check("pseudobulk_ok", pre("pseudobulk_ok"), None)
check("wilcoxon_ok", pre("wilcoxon_ok"), None)
check("synth (unarmed)", pre("synth"), None)
check("plain", pre("plain"), None)
check("nontool", pre("nontool"), None)

print("=== PreToolUse veto (thread t1 armed) ===")
T._THREAD_FETCH_FAIL.add("t1")
check("synth (armed)", pre("synth", "t1"), "SYNTHETIC_AFTER_FETCH_FAIL")
check("pseudorep_r (armed)", pre("pseudorep_r", "t1"), "PSEUDOREPLICATION_DE")

print("=== kill switch (ABA_PREEXEC_VETO=off) ===")
os.environ["ABA_PREEXEC_VETO"] = "off"
check("veto off", pre("pseudorep_r"), None)
del os.environ["ABA_PREEXEC_VETO"]

print("=== deny_to_result shape (model-facing blocked dict) ===")
d, _ = hooks.run_pre("run_r", {"code": CODE["pseudorep_r"][1]}, {})
res = hooks.deny_to_result(d)
check("blocked.status", res["status"], "blocked")
check("blocked.executed", res["executed"], False)
check("blocked.reason_code", res["reason_code"], "PSEUDOREPLICATION_DE")
check("blocked.has_message", bool(res.get("message")), True)
check("blocked.no_user_message (system_message unset)", "user_message" in res, False)

print("=== PostToolUse judgment warnings ===")
check("synth judgment warns", post_warns("synth"), 1)
check("plain judgment warns", post_warns("plain"), 0)

print("=== PostToolUse fetch-tool failure steer + veto chain (#316) ===")
def fetch_post(result, thread):
    hooks.run_post("fetch_url", {"url": "http://x"}, result, {"thread_id": thread})
    return result
r_fail = fetch_post({"status": "error", "error": "HTTP Error 403: Forbidden"}, "t2")
check("fetch fail -> fetch_warning", bool(r_fail.get("fetch_warning")), True)
check("fetch fail -> thread armed", "t2" in T._THREAD_FETCH_FAIL, True)
check("fetch fail -> later synth vetoed", pre("synth", "t2"), "SYNTHETIC_AFTER_FETCH_FAIL")
r_ok = fetch_post({"status": "ok", "content": "real data rows..."}, "t3")
check("fetch ok -> no warning", "fetch_warning" in r_ok, False)
check("fetch ok -> thread NOT armed", "t3" in T._THREAD_FETCH_FAIL, False)
r_200_404 = fetch_post({"status": "ok", "content": "<html>404 Not Found</html>"}, "t4")
check("fetch 200-wrapping-404 -> warning (content-aware)", bool(r_200_404.get("fetch_warning")), True)

print("=== PostToolUse synthetic-taint steer (#317, option a — taint on BUILD, fetch-fail-independent) ===")
def taint_warned(code, thread):
    res = {"returncode": 0, "stdout": "", "stderr": ""}
    hooks.run_post("run_python", {"code": code}, res, {"thread_id": thread})
    return any("BUILT synthetic" in w for w in res.get("guardrail_warnings", []))
SYNTH_BUILD = CODE["synth"][1]
DOWNSTREAM  = "import numpy as np\ncorr = np.corrcoef(X_adt.T)\nimport matplotlib.pyplot as plt\nplt.imshow(corr)"
DEMO_BUILD  = "# demo_mode\nimport numpy as np\ncounts=np.random.poisson(2,size=(500,200))\nadata=ad.AnnData(counts)"
check("synth build -> warned", taint_warned(SYNTH_BUILD, "t5"), True)
check("synth build -> thread tainted", "t5" in T._THREAD_SYNTH_TAINT, True)
check("downstream in tainted thread -> warned (analyze-already-faked)", taint_warned(DOWNSTREAM, "t5"), True)
check("clean thread -> not warned", taint_warned(DOWNSTREAM, "t7"), False)
check("demo build -> not warned", taint_warned(DEMO_BUILD, "t6"), False)
check("demo build -> thread NOT tainted (demo escape)", "t6" in T._THREAD_SYNTH_TAINT, False)

print("=== user-requested synthetic data EXEMPTS the guards (don't prohibit legit demos) ===")
SYN_INTENT = "please generate a synthetic dataset for a demo"
SYNTH = CODE["synth"][1]
def pre_intent(code, thread, intent):
    d, _ = hooks.run_pre("run_python", {"code": code}, {"thread_id": thread, "intent": intent})
    return d.reason_code if isinstance(d, hooks.Deny) else None
def post_intent(code, thread, intent):
    res = {"returncode": 0, "stdout": "", "stderr": ""}
    hooks.run_post("run_python", {"code": code}, res, {"thread_id": thread, "intent": intent})
    return res
T._THREAD_FETCH_FAIL.add("t8")
check("R2 veto SUPPRESSED when user asked for synthetic", pre_intent(SYNTH, "t8", SYN_INTENT), None)
check("R2 veto FIRES when no synthetic request", pre_intent(SYNTH, "t8", "process the sample with scanpy"), "SYNTHETIC_AFTER_FETCH_FAIL")
r_syn = post_intent(SYNTH, "t9", SYN_INTENT)
check("no taint when user asked for synthetic", "t9" in T._THREAD_SYNTH_TAINT, False)
check("no synth warnings when user asked for synthetic", len(r_syn.get("guardrail_warnings", [])), 0)

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}  ({len(PASS)} passed, {len(FAIL)} failed)")
sys.exit(1 if FAIL else 0)
