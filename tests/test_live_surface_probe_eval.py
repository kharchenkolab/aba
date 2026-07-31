"""Guards for the live surface probe's evaluation logic (pure part only — the
probe itself drives a real deployed server and is run via a subagent, never in
a test suite). Pins the failure classes the probe exists to catch:

  - produce-then-drop (an expected output kind missing from the manifest);
  - artifact_id collision across distinct outputs;
  - raw store-shard rows leaking (directory store not collapsed);
  - an advertised href that dead-links (404) vs an honest refusal (413);
  - transport vacuity (zero substrate-stamped execs must FAIL, not pass);
  - SSE caps must ACCUMULATE across resume hops (an error event emitted
    before an approval gate must survive the gate's resume stream).

Also keeps the probe's copied store-suffix tuple in sync with the backend's
(the probe deliberately does not import backend — it runs against a DEPLOYED
server from any checkout).
"""
from __future__ import annotations
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load_probe():
    p = _ROOT / "regtest" / "harness" / "live_surface_probe.py"
    spec = importlib.util.spec_from_file_location("live_surface_probe", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_surface_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


probe = _load_probe()


def _run(outputs, href_status=None, run_id="R1"):
    return {"run_id": run_id, "outputs": outputs,
            "href_status": href_status or {}}


_T = {"failures": [], "checked": 2}   # healthy transport evidence


def test_happy_path_mixed_passes_with_itemized_report():
    runs = [_run([
        {"kind": "figure", "label": "hist.png", "href": "/api/runs/R1/file?rel=hist.png",
         "artifact_id": "e1:plot:0"},
        {"kind": "table", "label": "rows.csv", "href": "/api/runs/R1/file?rel=rows.csv",
         "artifact_id": "e1:file:0"},
        {"kind": "store", "label": "out/data.zarr", "n_members": 40,
         "href": "/api/runs/R1/file?rel=out%2Fdata.zarr", "artifact_id": "e1:file:1"},
    ], {"/api/runs/R1/file?rel=hist.png": 200,
        "/api/runs/R1/file?rel=rows.csv": 200,
        "/api/runs/R1/file?rel=out%2Fdata.zarr": 413})]   # honest refusal is OK
    fails, lines = probe.evaluate_shape("mixed", {"figure": 1, "table": 1}, runs, _T)
    assert fails == []
    joined = "\n".join(lines)
    assert "figure=1" in joined and "table=1" in joined and "store=1" in joined
    assert "checked=2" in joined      # the substrate line is PRINTED, not silent


def test_missing_expected_kind_fails():
    runs = [_run([{"kind": "figure", "label": "hist.png"}])]
    fails, _ = probe.evaluate_shape("mixed", {"figure": 1, "table": 1}, runs, _T)
    assert any("'table'" in f for f in fails)


def test_shard_leak_detected_and_collapsed_store_clean():
    leaked = [_run([{"kind": "file", "label": "out/data.zarr/c/0/0"},
                    {"kind": "file", "label": "out/data.zarr/zarr.json"}])]
    fails, _ = probe.evaluate_shape("store", {}, leaked, _T)
    assert any("store-shard" in f for f in fails)
    collapsed = [_run([{"kind": "store", "label": "out/data.zarr", "n_members": 40}])]
    fails2, _ = probe.evaluate_shape("store", {"store": 1}, collapsed, _T)
    assert fails2 == []


def test_artifact_id_collision_fails():
    runs = [_run([
        {"kind": "file", "label": "a/x.json", "artifact_id": "e1:file:0"},
        {"kind": "file", "label": "b/x.json", "artifact_id": "e1:file:0"},
    ])]
    fails, _ = probe.evaluate_shape("mixed", {}, runs, _T)
    assert any("collision" in f for f in fails)


def test_dead_href_fails_honest_refusal_does_not():
    runs = [_run([{"kind": "table", "label": "t.csv", "href": "/api/runs/R1/f"}],
                 {"/api/runs/R1/f": 404, "/api/runs/R1/g": 413})]
    fails, _ = probe.evaluate_shape("mixed", {}, runs, _T)
    assert any("404" in f for f in fails)
    assert not any("413" in f for f in fails)


def test_transport_vacuous_pass_refused_and_failures_propagate():
    fails, _ = probe.evaluate_shape("mixed", {}, [_run([])],
                                    {"failures": [], "checked": 0})
    assert any("UNPROVEN" in f for f in fails)
    fails2, _ = probe.evaluate_shape(
        "mixed", {}, [_run([])],
        {"failures": ["transport:legacy_exec:R1/e9 substrate='local'"], "checked": 3})
    assert any("legacy_exec" in f for f in fails2)


class _FakeStream:
    def __init__(self, events):
        self._lines = ["data: " + json.dumps(e) for e in events]

    def iter_lines(self):
        return iter(self._lines)


def test_consume_accumulates_across_resume_hops():
    """Regression guard: the original probe REPLACED its capture dict on every
    resume hop, so an error event emitted before an approval gate vanished."""
    cap = {"run_id": None, "tools": [], "errors": [], "kinds": {}}
    probe.consume(_FakeStream([
        {"type": "tool_start", "name": "run_code"},
        {"type": "error", "message": "boom"},
        {"type": "delta", "run_id": "T1"},
    ]), cap)
    probe.consume(_FakeStream([{"type": "tool_start", "name": "keep"}]), cap)
    assert cap["errors"] and "boom" in cap["errors"][0]
    assert cap["tools"] == ["run_code", "keep"]
    assert cap["run_id"] == "T1"


def test_store_suffixes_in_sync_with_backend():
    src = (_ROOT / "backend" / "content" / "bio" / "lifecycle" / "runs.py").read_text()
    m = re.search(r"_STORE_DIR_SUFFIXES\s*=\s*(\([^)]*\))", src)
    assert m, "_STORE_DIR_SUFFIXES not found in backend runs.py"
    assert tuple(ast.literal_eval(m.group(1))) == probe.STORE_SUFFIXES


def test_every_shape_has_prompt_and_expectations():
    for name, spec in probe.SHAPES.items():
        assert spec.get("prompt"), name
        assert isinstance(spec.get("expect"), dict) and spec["expect"], name
