"""bio/tools/ cluster integrity (post-#3 split).

Two layers of defense against the bug class that landed in the live
session prj_e46347cc (register_dataset NameError because `_ctx_thread`
was used as a bare name in curation.py but defined in ctx_read.py):

  1. **Static**: pyflakes on every bio/tools/*.py. Catches ALL undefined
     names regardless of whether the function is ever called. This is
     the load-bearing test — pyflakes would have caught the
     original bug + the 6 other latent cross-cluster bugs that p11
     missed.

  2. **Dynamic**: call every ctx-using bio impl with a minimal payload.
     Defense-in-depth for cases pyflakes can't see (dynamic getattr,
     __globals__ lookups, etc.). Counts a non-NameError result as
     'callable' — the test isn't asserting business behaviour; just
     that the call doesn't trip on a missing module-level symbol.

  3. **Re-export surface**: every tool name reachable via
     `from content.bio.tools import X` (the public facade). Catches a
     missing entry in __init__.py's re-export blocks.

Run:
    .venv/bin/python tests/p13_bio_tools_imports.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_p13_")
# Isolate ALL filesystem side-effects to the temp tree, not just the DB.
# Bug history (2026-06-04): an early version of this file only set
# ABA_DB_PATH, leaving ABA_RUNTIME_DIR at its default — so when
# test_no_NameError_on_minimal_call invoked register_reference_tool with
# path="/tmp", refstore.register_reference happily ran shutil.copytree
# on the live /tmp into /workspace/aba-runtime/refs/<sha>/. Each test
# run dumped ~50-70 GB; six runs = ~304 GB of garbage in refs/. The DB
# rows didn't survive (the temp DB wiped), but the disk copy stayed.
#
# core.config reads ABA_RUNTIME_DIR at IMPORT time — must be set BEFORE
# any backend import below.
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402, F401


def test_pyflakes_no_undefined_names():
    """pyflakes on every cluster file under bio/tools/. Categorical
    catch for cross-cluster bugs (the original prj_e46347cc class):
    if a bare name like `_ctx_thread` is used in curation.py but
    isn't imported or defined there, pyflakes flags it.

    Skipped (with print) if pyflakes isn't installed — adding a hard
    dependency on a dev tool for a single test is too brittle."""
    try:
        import pyflakes.api  # noqa: F401
        import pyflakes.reporter
    except ImportError:
        print("[skip] pyflakes not installed; "
              ".venv/bin/python -m pip install pyflakes")
        return
    import io
    from pyflakes.api import checkPath
    cluster_files = sorted(
        (ROOT / "backend" / "content" / "bio" / "tools").glob("*.py"))
    # pyflakes.api.checkPath writes to sys.stderr; capture via Reporter.
    errors = io.StringIO()
    warnings = io.StringIO()
    reporter = pyflakes.reporter.Reporter(warnings, errors)
    n_warnings = 0
    for p in cluster_files:
        n_warnings += pyflakes.api.checkPath(str(p), reporter=reporter)
    out = warnings.getvalue() + errors.getvalue()
    # Filter to "undefined name" specifically — that's the bug class
    # we're guarding. Unused imports, redefinitions etc. are noise
    # for this test (they're real issues but outside our scope).
    undef_lines = [
        line for line in out.splitlines()
        if "undefined name" in line or "may be undefined" in line
    ]
    assert not undef_lines, (
        f"pyflakes found {len(undef_lines)} undefined-name issue(s) in "
        f"bio/tools/ cluster files:\n  " + "\n  ".join(undef_lines)
    )


def test_every_tool_importable_from_facade():
    """All 46 + helper names are reachable via `from content.bio.tools
    import X` (the re-export pattern). Catches a missing entry in
    __init__.py's re-export blocks."""
    import content.bio.tools as t
    expected_tool_names = [
        # run_exec
        "run_python", "run_r",
        # simple
        "list_capabilities_tool", "read_memory_tool", "search_pypi",
        # ctx_read
        "skill_tool", "read_skill", "list_entities_tool",
        "get_provenance", "get_dependents",
        "read_capability", "read_csv_info",
        # plan_etc
        "create_scenario", "present_plan", "ask_clarification",
        "write_memory_tool", "restart_kernel_tool", "run_nextflow",
        # discovery
        "search_skills_tool", "search_bioconda", "search_nf_core",
        "search_mcp_registry", "inspect_package", "ensure_capability",
        "propose_capability_tool", "fetch_url", "fetch_ensembl",
        "lookup_sra_runinfo",
        # file_io
        "list_data_files", "inspect_upload",
        "write_file_tool", "edit_file_tool", "read_file_tool",
        # curation
        "register_reference_tool", "find_reference_tool",
        "register_dataset_tool", "add_to_dataset_tool", "remove_from_dataset_tool",
        "pin_entity_tool", "promote_to_result_tool",
        "create_finding_tool", "create_claim_tool",
        "open_run_tool", "close_run_tool",
        "annotate_entity_tool", "_archive_entity_tool",
    ]
    missing = [n for n in expected_tool_names if not hasattr(t, n)]
    assert not missing, f"missing re-exports: {missing}"


def test_no_NameError_on_minimal_call():
    """Smoke-call each ctx-using bio impl with a minimal payload + a
    fake ctx. Asserts the call returns SOMETHING (dict) — not that the
    business logic is correct. A NameError here means a bare reference
    to a moved helper (the prj_e46347cc bug class)."""
    import content.bio.tools as t
    ctx = {"thread_id": "default", "active_tools": []}

    cases: list[tuple[str, dict, dict]] = [
        # curation cluster (the cluster the live bug hit)
        ("register_dataset_tool", {"title": "t", "path": "/tmp"}, ctx),
        ("add_to_dataset_tool",    {"dataset_id": "x", "paths": []}, ctx),
        ("remove_from_dataset_tool", {"dataset_id": "x", "paths": []}, ctx),
        ("pin_entity_tool",        {"entity_id": "no_such"}, ctx),
        ("promote_to_result_tool", {"figure_id": "no_such", "interpretation": "x"}, ctx),
        ("create_finding_tool",    {"result_ids": ["no_such"], "text": "x"}, ctx),
        ("create_claim_tool",      {"statement": "x"}, ctx),
        ("annotate_entity_tool",   {"entity_id": "no_such", "tags": []}, ctx),
        ("_archive_entity_tool",   {"entity_id": "no_such"}, ctx),
        ("open_run_tool",          {"title": "t"}, ctx),
        ("close_run_tool",         {}, ctx),
        ("register_reference_tool", {"path": "/tmp"}, ctx),
        ("find_reference_tool",    {"all": False}, ctx),
        # ctx_read cluster
        ("skill_tool",             {"skill": "no_such"}, ctx),
        ("read_skill",             {"name": "no_such"}, ctx),
        ("list_entities_tool",     {"limit": 5}, ctx),
        # discovery cluster (those that take ctx)
        ("inspect_package",        {"name": "json"}, ctx),
        # file_io
        ("write_file_tool",        {"path": "x", "body": "y"}, ctx),
        ("read_file_tool",         {"path": "x"}, ctx),
        ("edit_file_tool",         {"path": "x", "old_string": "a", "new_string": "b"}, ctx),
        # plan_etc
        ("restart_kernel_tool",    {}, ctx),
    ]
    failures: list[str] = []
    for name, inp, c in cases:
        fn = getattr(t, name, None)
        if fn is None:
            failures.append(f"{name}: not found in facade")
            continue
        try:
            fn(inp, c)
        except NameError as e:
            failures.append(f"{name}: NameError → {e}")
        except TypeError:
            # The function may take only (input_) — retry without ctx.
            try:
                fn(inp)
            except NameError as e2:
                failures.append(f"{name}: NameError (no-ctx) → {e2}")
            except Exception:
                pass     # any non-NameError means the helper IS reachable
        except Exception:
            # Anything else (KeyError, ValueError, DB lookups failing on
            # the fake input) means the helper IS reachable — that's all
            # we're asserting.
            pass
    assert not failures, "cross-cluster reference bugs:\n  " + "\n  ".join(failures)


def main() -> int:
    tests = [
        test_pyflakes_no_undefined_names,
        test_every_tool_importable_from_facade,
        test_no_NameError_on_minimal_call,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}:\n{e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
