#!/usr/bin/env python3
"""Generate docs/arch/settings-reference.md from the config settings registry.

The reference is the single, generated catalogue of every ABA_* setting the
backend reads (env_reorg). Run from the repo root:

    backend-python scripts/gen_settings_reference.py

It resolves defaults with a NEUTRAL environment (ABA_* stripped) so the doc shows
declared defaults, not the generating machine's paths.
"""
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from core import config as c  # noqa: E402

# Strip ABA_* (+ the non-ABA setting keys) so path/scalar defaults render neutrally.
for _k in list(os.environ):
    if _k.startswith("ABA_"):
        del os.environ[_k]
for _k in ("DATA_DIR", "ARTIFACTS_DIR", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

CAT_ORDER = ["paths", "deploy", "mode", "model", "credentials", "behavior",
             "experimental", "tuning", "cluster", "nextflow", "bundle"]
CAT_BLURB = {
    "paths": "Filesystem roots and directories.",
    "deploy": "Container / offload / module wiring injected by the installer or OOD launcher.",
    "mode": "Whole-process modes (SINGLE DB, runtime backend, tool kill-switch).",
    "model": "Model selection + LLM request shape (Reasoning plane; aba-owned).",
    "credentials": "Credential inputs + provider config (secrets redacted).",
    "behavior": "Feature flags / behavior toggles.",
    "experimental": "Experimental gates (Phase-7 resolve-flag candidates).",
    "tuning": "Numeric / non-behavioral tuning knobs.",
    "cluster": "Cluster & job placement (weft absorbs most of this).",
    "nextflow": "Nextflow execution wiring (self-contained compute subsystem).",
    "bundle": "Bundle scope resolution + site config.",
}


def esc(x):
    return str(x).replace("|", "\\|")


def neutral_default(name):
    s = c.get_setting(name)
    if s.secret:
        return "•redacted•"
    val, _ = s.resolve()
    if val is None:
        return ""
    if s.type == "path":
        return esc(val)
    return esc(val)


def main():
    rows = c.list_settings(reveal_secrets=False)["settings"]
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    out = []
    w = out.append
    w("# Settings reference")
    w("")
    w("> **Generated** from the `core/config.py` settings registry (`list_settings()`), the")
    w("> single enforced read path for every `ABA_*` var the backend consumes. Regenerate with")
    w("> `scripts/gen_settings_reference.py` (or view live with `aba settings`). Do not hand-edit.")
    w("")
    w("Each setting is declared once via `setting()` and read through "
      "`config.settings.<name>.get()`. Columns:")
    w("")
    w("- **env** — the environment variable(s) read (first present wins).")
    w("- **type** / **default** — coercion + value when unset (deployment-neutral).")
    w("- **weft_fate** — what the future weft compute-substrate rewrite does with it "
      "(`keep` / `retire` / `move:site` / `move:envspec` / `revisit`).")
    w("- **reduction** — the fewer-better-variables plan (`keep` / `dead` / `resolve-flag` / "
      "`merge:<group>` / `derive:<from>` / `relocate:<layer>`).")
    w("- **flags** — `branches` (changes behavior), `secret` (redacted), `deploy` "
      "(launcher-forwarded / `deploy_injected`).")
    w("")
    wf = Counter(r["weft_fate"] for r in rows)
    rd = Counter(r["reduction"].split(":")[0] for r in rows)
    w(f"**{len(rows)} settings** across {len(by_cat)} categories.  ")
    w("weft_fate — " + ", ".join(f"`{k}` {v}" for k, v in sorted(wf.items())) + ".  ")
    w("reduction — " + ", ".join(f"`{k}` {v}" for k, v in sorted(rd.items())) + ".")
    w("")

    for cat in CAT_ORDER + [x for x in by_cat if x not in CAT_ORDER]:
        if cat not in by_cat:
            continue
        w(f"## {cat}")
        w("")
        w(f"*{CAT_BLURB.get(cat, '')}*")
        w("")
        w("| setting | env | type | default | weft_fate | reduction | flags | doc |")
        w("|---|---|---|---|---|---|---|---|")
        for r in sorted(by_cat[cat], key=lambda x: x["name"]):
            flags = []
            if r["branches"]:
                flags.append("branches")
            if r["secret"]:
                flags.append("secret")
            if r["deploy_injected"]:
                flags.append("deploy")
            envs = " ".join("`" + e + "`" for e in r["env"])
            w(f"| `{r['name']}` | {envs} | {r['type']} | {neutral_default(r['name'])} "
              f"| {r['weft_fate']} | {r['reduction']} | {' '.join(flags)} | {esc(r['doc'])} |")
        w("")

    (ROOT / "docs" / "arch" / "settings-reference.md").write_text("\n".join(out) + "\n")
    print(f"wrote docs/arch/settings-reference.md ({len(rows)} settings)")


if __name__ == "__main__":
    main()
