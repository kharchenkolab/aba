"""
Item 4 — biomni as an EXTRACTED REFERENCE catalogue (collections.md).

biomni is mined offline into a file-backed reference layer, NOT a runtime
dependency:
  1. The collection loads as reference capabilities (origin=biomni, no
     provisioning, a source_ref back to the implementation for the lakeFS lift).
  2. search_capabilities ranks biomni tools by intent, alongside DB entities
     (entity caps like gseapy still surface — biomni doesn't drown them).
  3. read_capability returns the approach + params + source_ref.
  4. ensure_capability on a reference cap returns 'reference' (doesn't pretend
     to install) — biomni is not runnable here.

Deterministic (no model, no network). Run:
    .venv/bin/python tests/d3_biomni_catalogue.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d3_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d3.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                # noqa: E402
import content.bio  # noqa: E402,F401
from core.catalog import (                            # noqa: E402
    collection_capabilities, search_capabilities, resolve_capability, collection_domains,
)
from content.bio.tools import read_capability, ensure_capability  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_catalogue_loaded():
    print("biomni reference catalogue loads")
    caps = collection_capabilities()
    check("many caps loaded (>150)", len(caps) > 150, str(len(caps)))
    check("all biomni, all reference, no provisioning",
          all(c.get("origin") == "biomni" and c.get("reference") and not c.get("provisioning")
              for c in caps))
    domains = collection_domains().get("biomni", [])
    check("multiple domains (>15)", len(domains) > 15, str(len(domains)))
    check("domains include genomics + pharmacology",
          "genomics" in domains and "pharmacology" in domains, str(domains[:6]))


def test_intent_search():
    print("intent search over the reference catalogue")
    hits = [c["name"] for c in search_capabilities("annotate cell types from single-cell RNA-seq")]
    check("ranks a biomni annotation tool", any("annotate_celltype" in h for h in hits), str(hits[:4]))
    # entity caps must still surface — biomni shouldn't drown them
    enr = [c["name"] for c in search_capabilities("gene set enrichment analysis")]
    check("entity cap gseapy still found", "gseapy" in enr, str(enr[:5]))
    de = [c["name"] for c in search_capabilities("differential expression")]
    check("entity cap pydeseq2 still found", "pydeseq2" in de, str(de[:5]))


def test_resolve_and_read():
    print("resolve + read_capability")
    cap = resolve_capability("annotate_celltype_scRNA")
    check("resolve finds a biomni cap by name", cap is not None and cap.get("origin") == "biomni", str(bool(cap)))
    r = read_capability({"name": "annotate_celltype_scRNA"})
    check("read returns reference + source_ref",
          r.get("reference") and r.get("source_ref", "").startswith("biomni/tool/"), str(r.get("source_ref")))
    check("read returns params", isinstance(r.get("required_params"), list) and len(r["required_params"]) > 0, str(r.get("required_params"))[:80])
    check("read nudges toward ABA capabilities", "ABA capabilities" in (r.get("note") or ""))
    check("unknown name -> not_found", read_capability({"name": "no_such_tool_xyz"}).get("status") == "not_found")


def test_not_runnable_via_biomni():
    print("reference caps are not runnable via biomni")
    e = ensure_capability({"name": "annotate_celltype_scRNA"})
    check("ensure_capability -> reference (not 'ready')", e.get("status") == "reference", str(e.get("status")))
    check("explains it's extracted, not installable", "extracted" in (e.get("note") or ""), str(e.get("note"))[:80])


def main() -> int:
    init_db()
    test_catalogue_loaded()
    test_intent_search()
    test_resolve_and_read()
    test_not_runnable_via_biomni()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL BIOMNI-CATALOGUE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
