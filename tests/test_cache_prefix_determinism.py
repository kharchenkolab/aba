"""Guard: the cached prompt prefix (system block + full tool defs) is BYTE-STABLE
across processes / hash seeds.

Anthropic prompt caching keys on the literal prefix bytes. If any segment of the
system prompt or a tool's rendered schema comes from a Python set/dict iterated in
hash order, the bytes differ per process (PYTHONHASHSEED is per-process) → a
prompt-cache MISS on the first turn after every server restart. This guard assembles
the prefix in two subprocesses with different PYTHONHASHSEED and asserts they're
byte-identical — it would have caught the cache-prefix-nondeterminism finding, and
catches any future set/dict rendered into the cached prefix.

Slow-ish (two in-process app boots). Standalone: `python tests/test_cache_prefix_determinism.py`.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mask(s: str) -> str:
    s = re.sub(r'\b(prj|thr|run|ana|fig|res|cl|ds|nb|oq|job)_[0-9a-f]{6,}\b', r'\1_ID', s)
    s = re.sub(r'/tmp/aba_cpd_[A-Za-z0-9_]+', '/TMP', s)
    return s


def _assemble_prefix() -> str:
    """Drive one FAKE turn (gateway connected) + render the full tool catalog;
    return the masked system block + full tool defs — the real cached bytes."""
    _tmp = tempfile.mkdtemp(prefix="aba_cpd_")
    os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/list_files.jsonl")
    os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "t.db")
    os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
    os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
    os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
    os.environ["ABA_TURN_LOG_DIR"] = str(Path(_tmp) / "turnlog")
    os.environ["ABA_RUNTIME_DIR"] = _tmp
    os.environ.setdefault("DATA_DIR", str(ROOT / "backend/data"))
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient
    from main import app
    from core.runtime.mcp import list_tools
    from guide import _PRIORITY_TOOLS
    with TestClient(app) as client:                 # lifespan → gateway connected
        with client.stream("POST", "/api/chat",
                           json={"text": "what files are here?", "thread_id": "default",
                                 "focus_entity_id": "workspace"}) as r:
            for _ in r.iter_lines():
                pass
        defs = list_tools(mode="full", priority_tools=_PRIORITY_TOOLS)
    files = sorted(glob.glob(os.path.join(os.environ["ABA_TURN_LOG_DIR"], "*_run_*.json")))
    system = json.loads(Path(files[-1]).read_text()).get("system") or ""
    defs_blob = json.dumps(defs, default=str, ensure_ascii=False, indent=1)
    return _mask(system) + "\n===TOOL_DEFS===\n" + _mask(defs_blob)


# Child mode: assemble under whatever PYTHONHASHSEED we were launched with, write, exit.
if os.environ.get("ABA_CPD_CHILD"):
    Path(sys.argv[1]).write_text(_assemble_prefix())
    sys.exit(0)


try:
    import pytest
    pytestmark = pytest.mark.platform

    def _fail(m):
        pytest.fail(m)
except ImportError:
    def _fail(m):
        raise AssertionError(m)


def _child(seed: int, out: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ABA_FAKE", "ABA_DB", "ABA_RUNTIME", "ABA_ENVS", "ABA_TURN_LOG", "ABA_WORK", "ARTIFACTS_DIR"))}
    env["PYTHONHASHSEED"] = str(seed)
    env["ABA_CPD_CHILD"] = "1"
    subprocess.run([sys.executable, __file__, out], env=env, check=True, cwd=str(ROOT),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_cache_prefix_byte_stable_across_hash_seeds():
    d = tempfile.mkdtemp(prefix="aba_cpd_out_")
    a, b = os.path.join(d, "s0.txt"), os.path.join(d, "s1.txt")
    _child(0, a)
    _child(1, b)
    ta, tb = Path(a).read_text(), Path(b).read_text()
    if ta != tb:
        import difflib
        diff = "\n".join(list(difflib.unified_diff(
            ta.splitlines(), tb.splitlines(), "seed0", "seed1", lineterm=""))[:40])
        _fail("cached prompt prefix is NOT byte-stable across hash seeds — a set/dict is "
              "rendered into the cached system/tools, so the prompt-cache misses on every "
              "server restart. Sort the offending segment at the source:\n" + diff)


if __name__ == "__main__":
    test_cache_prefix_byte_stable_across_hash_seeds()
    print("PASS cache prefix byte-stable across hash seeds")
