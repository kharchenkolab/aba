"""P2 unit tests for nf-core parameter-schema parse + validation
(core.exec.nextflow_schema). Fixture-based — no network. Live fetch is exercised
opt-in by tests/live_nextflow_hpc.py / a describe_pipeline call against nf-core/rnaseq.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec import nextflow_schema as ns  # noqa: E402

# A trimmed nf-core-style schema: grouped under `definitions`, per-group `required`.
SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "definitions": {
        "input_output_options": {
            "title": "Input/output options",
            "required": ["input", "outdir"],
            "properties": {
                "input": {"type": "string", "description": "Path to the samplesheet"},
                "outdir": {"type": "string", "description": "Output directory"},
                "email": {"type": "string", "description": "Notification email"},
            },
        },
        "reference_genome_options": {
            "title": "Reference genome",
            "properties": {
                "genome": {"type": "string", "enum": ["GRCh38", "GRCm38"], "description": "iGenomes key"},
                "save_reference": {"type": "boolean", "default": False},
            },
        },
        "generic_options": {
            "title": "Generic",
            "properties": {"max_cpus": {"type": "integer", "default": 16}},
        },
    },
}


def test_parse_params():
    ps = {p["name"]: p for p in ns.parse_params(SCHEMA)}
    assert set(ps) == {"input", "outdir", "email", "genome", "save_reference", "max_cpus"}
    assert ps["input"]["required"] and ps["input"]["group"] == "Input/output options"
    assert ps["email"]["required"] is False
    assert ps["genome"]["enum"] == ["GRCh38", "GRCm38"]
    assert ps["max_cpus"]["type"] == "integer" and ps["max_cpus"]["default"] == 16


def test_required_params():
    assert ns.required_params(SCHEMA) == {"input", "outdir"}


def test_validate_missing_required():
    r = ns.validate_params(SCHEMA, {})           # nothing supplied
    assert r["ok"] is False
    assert any("--input" in e for e in r["errors"])
    assert not any("--outdir" in e for e in r["errors"])   # ABA injects outdir → not flagged


def test_validate_enum_and_type():
    r = ns.validate_params(SCHEMA, {"input": "s.csv", "genome": "hg99"})
    assert r["ok"] is False and any("genome" in e and "not allowed" in e for e in r["errors"])
    r2 = ns.validate_params(SCHEMA, {"input": "s.csv", "max_cpus": "lots"})
    assert r2["ok"] is False and any("max_cpus" in e and "integer" in e for e in r2["errors"])
    r3 = ns.validate_params(SCHEMA, {"input": "s.csv", "max_cpus": "8", "genome": "GRCh38"})
    assert r3["ok"] is True and not r3["errors"]            # string-int + valid enum → ok


def test_validate_unknown_is_warning_not_error():
    r = ns.validate_params(SCHEMA, {"input": "s.csv", "frobnicate": "yes"})
    assert r["ok"] is True                                  # unknown params don't block
    assert any("frobnicate" in w for w in r["warnings"])


def test_type_ok():
    assert ns._type_ok("8", "integer") and not ns._type_ok("x", "integer")
    assert ns._type_ok("1.5", "number") and not ns._type_ok("x", "number")
    assert ns._type_ok("true", "boolean") and ns._type_ok(False, "boolean")
    assert ns._type_ok("anything", "string") and ns._type_ok("anything", None)


def test_fetch_schema_cached(monkeypatch):
    calls = {"n": 0}
    def fake_get(url, as_json=True):
        calls["n"] += 1
        return SCHEMA if url.endswith("/master/nextflow_schema.json") else None
    monkeypatch.setattr(ns, "_get", fake_get)
    ns._SCHEMA_CACHE.clear()
    s = ns.fetch_schema("nf-core/demo")
    assert s is SCHEMA and ns.required_params(s) == {"input", "outdir"}
    n_after_first = calls["n"]
    ns.fetch_schema("nf-core/demo")                         # cached → no new HTTP
    assert calls["n"] == n_after_first


def test_fetch_schema_no_repo_path():
    assert ns.fetch_schema("hello") is None                 # needs owner/repo


def test_param_form():
    groups = ns.param_form(SCHEMA)
    assert isinstance(groups, list) and [g["group"] for g in groups][:1] == ["Input/output options"]
    io = next(g for g in groups if g["group"] == "Input/output options")
    inp = next(p for p in io["params"] if p["name"] == "input")
    assert inp["required"] and set(inp) == {"name", "type", "required", "default", "enum", "help"}
    ref = next(p for g in groups for p in g["params"] if p["name"] == "genome")
    assert ref["enum"] == ["GRCh38", "GRCm38"]


def test_enrich_plan_steps(monkeypatch):
    monkeypatch.setattr(ns, "fetch_schema", lambda *a, **k: SCHEMA)
    steps = [
        {"n": 1, "title": "QC", "skill": "bp-quality-control", "parameters": {}},
        {"n": 2, "title": "RNA-seq", "skill": "run_nextflow",
         "parameters": {"pipeline": "nf-core/rnaseq", "revision": "3.26.0", "params": {"input": "s.csv"}}},
    ]
    out = ns.enrich_plan_steps(steps)
    assert "param_form" not in out[0]                          # non-pipeline step untouched
    s2 = out[1]
    assert s2["pipeline"] == "nf-core/rnaseq" and s2["revision"] == "3.26.0"
    assert s2["prefilled"] == {"input": "s.csv"}
    assert isinstance(s2["param_form"], list)
    assert any(g["group"] == "Input/output options" for g in s2["param_form"])
    names = {p["name"] for g in s2["param_form"] for p in g["params"]}
    assert "input" in names and "outdir" not in names      # outdir is ABA-set → excluded from the form


def test_enrich_plan_steps_no_schema(monkeypatch):
    monkeypatch.setattr(ns, "fetch_schema", lambda *a, **k: None)
    out = ns.enrich_plan_steps(
        [{"n": 1, "title": "x", "skill": "run_nextflow", "parameters": {"pipeline": "nf-core/x"}}])
    assert "param_form" not in out[0]                          # no schema → plain step, no crash


# A trimmed nf-core-style samplesheet schema (assets/schema_input.json): an array of
# row objects; columns under items.properties, required under items.required.
INPUT_SCHEMA = {
    "type": "array",
    "description": "Samplesheet (CSV) for the rnaseq pipeline",
    "items": {
        "type": "object",
        "properties": {
            "sample": {"type": "string", "pattern": "^\\S+$", "errorMessage": "Sample name; no spaces", "meta": ["id"]},
            "fastq_1": {"type": "string", "format": "file-path", "errorMessage": "R1 fastq.gz must be provided"},
            "fastq_2": {"type": "string", "format": "file-path", "errorMessage": "R2 fastq.gz (optional)"},
            "strandedness": {"type": "string", "enum": ["forward", "reverse", "unstranded", "auto"],
                             "errorMessage": "strandedness"},
        },
        "required": ["sample", "fastq_1", "strandedness"],
    },
}


def test_parse_input_columns():
    cols = ns.parse_input_columns(INPUT_SCHEMA)
    assert [c["name"] for c in cols] == ["sample", "fastq_1", "fastq_2", "strandedness"]  # order kept
    by = {c["name"]: c for c in cols}
    assert by["sample"]["required"] and by["fastq_1"]["required"] and by["fastq_2"]["required"] is False
    assert by["fastq_1"]["format"] == "file-path"
    assert by["strandedness"]["enum"] == ["forward", "reverse", "unstranded", "auto"]
    assert by["fastq_1"]["help"]                                # help carried (from errorMessage)


def test_fetch_input_schema_cached(monkeypatch):
    calls = {"n": 0}
    def fake_get(url, as_json=True):
        calls["n"] += 1
        return INPUT_SCHEMA if url.endswith("/master/assets/schema_input.json") else None
    monkeypatch.setattr(ns, "_get", fake_get)
    ns._INPUT_SCHEMA_CACHE.clear()
    assert ns.fetch_input_schema("nf-core/demo") is INPUT_SCHEMA
    n1 = calls["n"]
    ns.fetch_input_schema("nf-core/demo")                       # cached → no new HTTP
    assert calls["n"] == n1
    assert ns.fetch_input_schema("hello") is None               # needs owner/repo


def test_pipeline_doc_links():
    d = ns.pipeline_doc_links("nf-core/rnaseq", "3.26.0")
    assert d["usage"] == "https://raw.githubusercontent.com/nf-core/rnaseq/3.26.0/docs/usage.md"
    assert d["output"].endswith("/3.26.0/docs/output.md")
    assert d["repo"] == "https://github.com/nf-core/rnaseq" and d["homepage"] == "https://nf-co.re/rnaseq"
    d2 = ns.pipeline_doc_links("someuser/mypipe")               # non-nf-core: repo+readme+docs, no homepage
    assert d2["repo"] == "https://github.com/someuser/mypipe" and "homepage" not in d2
    assert ns.pipeline_doc_links("hello") == {}                 # needs owner/repo


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
