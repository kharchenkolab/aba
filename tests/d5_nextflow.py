"""
Basic Nextflow / nf-core support (task #233).

  1. _nextflow_command builds a correct `nextflow run …` argv.
  2. run_nextflow validates input + reserves the remote/HPC path (not wired yet).
  3. ensure_capability on a pipeline-archetype cap installs nextflow and reports
     ready (materialize mocked — no real conda install in the unit test).
  4. (opt-in, ABA_NEXTFLOW_LIVE=1) a real `nextflow run nextflow-io/hello`.

Deterministic by default (no network/conda). Run:
    .venv/bin/python tests/d5_nextflow.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d5_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d5.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import (                        # noqa: E402
    _nextflow_command, run_nextflow, propose_capability_tool, ensure_capability,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_command_builder():
    print("nextflow command builder")
    cmd = _nextflow_command("nf-core/rnaseq", revision="3.14.0", profile="test,docker",
                            outdir="/o", params={"genome": "GRCh38", "input": "s.csv"})
    check("starts with nextflow run <pipeline>", cmd[:3] == ["nextflow", "run", "nf-core/rnaseq"], str(cmd[:3]))
    check("includes -r revision", "-r" in cmd and cmd[cmd.index("-r") + 1] == "3.14.0")
    check("includes -profile", "-profile" in cmd and cmd[cmd.index("-profile") + 1] == "test,docker")
    check("includes --outdir", "--outdir" in cmd and cmd[cmd.index("--outdir") + 1] == "/o")
    check("maps params to --key value", "--genome" in cmd and cmd[cmd.index("--genome") + 1] == "GRCh38")
    check("non-interactive log flag", "-ansi-log" in cmd and cmd[cmd.index("-ansi-log") + 1] == "false")
    # minimal form
    bare = _nextflow_command("nextflow-io/hello", outdir="/o")
    check("bare form has no -r/-profile", "-r" not in bare and "-profile" not in bare, str(bare))


def test_input_validation_and_remote_seam():
    print("input validation + HPC seam")
    check("missing pipeline -> error", run_nextflow({}).get("status") == "error")
    check("remote -> unsupported (HPC not wired)",
          run_nextflow({"pipeline": "nf-core/rnaseq", "remote": True}).get("status") == "unsupported_location")
    check("background -> unsupported (HPC not wired)",
          run_nextflow({"pipeline": "nf-core/rnaseq", "background": True}).get("status") == "unsupported_location")


def test_ensure_pipeline_installs_nextflow():
    print("ensure_capability(pipeline) installs nextflow (materialize mocked)")
    from core.exec import MaterializingExecutor
    calls = {}
    orig = MaterializingExecutor.materialize

    def fake_materialize(self, prov, scope="system", cancel_token=None):
        calls["conda"] = (prov.conda or {}).get("spec")
        return None

    MaterializingExecutor.materialize = fake_materialize
    try:
        propose_capability_tool({"name": "nf-core-rnaseq", "archetype": "pipeline",
                                 "url": "https://nf-co.re/rnaseq"})
        res = ensure_capability({"name": "nf-core-rnaseq"})
        check("ensure -> ready", res.get("status") == "ready", str(res))
        check("installed nextflow via conda", calls.get("conda") == "nextflow", str(calls))
        check("note points at run_nextflow", "run_nextflow" in (res.get("note") or ""))
    finally:
        MaterializingExecutor.materialize = orig


def test_container_precheck():
    print("F6: container-engine pre-check (fail fast, no timeout)")
    from content.bio.tools import _nextflow_env_blocker, _available_container_engines
    avail = _available_container_engines()
    check("trivial pipeline (hello) never blocked", _nextflow_env_blocker("nextflow-io/hello", None) is None)
    missing = next((e for e in ("docker", "singularity", "apptainer") if e not in avail), None)
    if missing:
        b = _nextflow_env_blocker("nf-core/rnaseq", missing)
        check("requested-but-missing engine -> blocked", (b or {}).get("status") == "unsupported_environment", str(b))
        # run_nextflow returns immediately (before installing nextflow / launching)
        r = run_nextflow({"pipeline": "nf-core/rnaseq", "profile": missing})
        check("run_nextflow short-circuits (no install, no timeout)",
              r.get("status") == "unsupported_environment", str(r)[:160])
    if not avail:
        check("nf-core with no backend + no engine -> blocked",
              (_nextflow_env_blocker("nf-core/rnaseq", "test") or {}).get("status") == "unsupported_environment")


def test_live_hello():
    if os.environ.get("ABA_NEXTFLOW_LIVE") != "1":
        print("live hello: SKIPPED (set ABA_NEXTFLOW_LIVE=1 to run)")
        return
    print("live: nextflow run nextflow-io/hello")
    res = run_nextflow({"pipeline": "nextflow-io/hello", "timeout_s": 1800})
    check("hello pipeline succeeds", res.get("status") == "ok" and res.get("returncode") == 0, str(res)[:300])


def main() -> int:
    init_db()
    test_command_builder()
    test_input_validation_and_remote_seam()
    test_ensure_pipeline_installs_nextflow()
    test_container_precheck()
    test_live_hello()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL NEXTFLOW CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
