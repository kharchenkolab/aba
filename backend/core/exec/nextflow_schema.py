"""Nextflow / nf-core parameter schema (P2 — fail-fast + agent guidance).

nf-core pipelines ship a machine-readable ``nextflow_schema.json`` (JSON-Schema +
nf-core conventions: params grouped under ``definitions``/``$defs``, each with its
own ``required`` list, plus type/default/enum/help per param). We fetch + parse it
to (a) VALIDATE the agent's params before anything hits Slurm — no doomed submits —
and (b) DESCRIBE a pipeline's inputs to the agent/user (describe_pipeline).

Best-effort throughout: a pipeline with no schema, or a fetch miss, simply skips
validation (never blocks a run on a network hiccup). Outbound HTTP confirmed open
on the cluster login node; results are cached in-process per (pipeline, revision).
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

_TIMEOUT = 12
# In-process caches (the tool call runs in the backend process; the head job on the
# compute node never re-validates — validation is pre-submit).
_SCHEMA_CACHE: dict[tuple, Optional[dict]] = {}
_RELEASE_CACHE: dict[str, Optional[str]] = {}

# ABA always injects these on the `nextflow run` line itself, so they're never
# "missing" even when the schema marks them required and the agent omits them.
_AUTO_PROVIDED = {"outdir"}


def _get(url: str, *, as_json: bool = True):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aba-nextflow"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            if getattr(r, "status", r.getcode()) != 200:
                return None
            body = r.read().decode("utf-8", "replace")
        return json.loads(body) if as_json else body
    except Exception:  # noqa: BLE001 — network/parse miss → caller degrades gracefully
        return None


def fetch_schema(pipeline: str, revision: Optional[str] = None) -> Optional[dict]:
    """Fetch a pipeline's nextflow_schema.json (raw GitHub), trying the pinned
    revision then the default branches. Returns the parsed dict or None (no schema /
    not fetchable). Cached per (pipeline, revision)."""
    pipeline = (pipeline or "").strip().strip("/")
    if "/" not in pipeline:                       # need owner/repo to locate the repo file
        return None
    key = (pipeline, revision)
    if key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[key]
    refs = [r for r in (revision, "master", "main") if r]
    schema = None
    for ref in refs:
        url = f"https://raw.githubusercontent.com/{pipeline}/{ref}/nextflow_schema.json"
        d = _get(url)
        if isinstance(d, dict) and (d.get("definitions") or d.get("$defs") or d.get("properties")):
            schema = d
            break
    _SCHEMA_CACHE[key] = schema
    return schema


def _groups(schema: dict) -> dict:
    """The param GROUPS — nf-core nests them under definitions/$defs; each is an
    object with its own properties + required. Falls back to a single synthetic
    group from top-level properties."""
    g = schema.get("definitions") or schema.get("$defs")
    if isinstance(g, dict) and g:
        return g
    if schema.get("properties"):
        return {"_root": {"title": "Parameters", "properties": schema["properties"],
                          "required": schema.get("required", [])}}
    return {}


def parse_params(schema: dict) -> list[dict]:
    """Flatten the schema into a list of param specs:
    {name, type, required, default, enum, help, group}. Stable, UI/agent-friendly."""
    out: list[dict] = []
    top_required = set(schema.get("required") or [])
    for _gkey, grp in _groups(schema).items():
        if not isinstance(grp, dict):
            continue
        title = grp.get("title") or _gkey
        req = set(grp.get("required") or []) | top_required
        for name, spec in (grp.get("properties") or {}).items():
            if not isinstance(spec, dict):
                continue
            out.append({
                "name": name,
                "type": spec.get("type"),
                "required": name in req,
                "default": spec.get("default"),
                "enum": spec.get("enum"),
                "help": (spec.get("description") or spec.get("help_text") or "").strip(),
                "group": title,
            })
    return out


def required_params(schema: dict) -> set:
    return {p["name"] for p in parse_params(schema) if p["required"]}


def _type_ok(value, jtype: Optional[str]) -> bool:
    """Pragmatic type check (params often arrive as strings). Only flags a CLEAR
    mismatch; unknown/absent types pass."""
    if jtype in (None, "string", "object", "array"):
        return True
    s = str(value).strip()
    if jtype == "boolean":
        return s.lower() in ("true", "false", "1", "0") or isinstance(value, bool)
    if jtype == "integer":
        try:
            int(s); return True
        except ValueError:
            return False
    if jtype == "number":
        try:
            float(s); return True
        except ValueError:
            return False
    return True


def validate_params(schema: dict, params: Optional[dict]) -> dict:
    """Validate the agent's ``params`` against the schema. Returns
    {ok, errors, warnings}. Hard ERRORS (block the run): a missing required param,
    a value outside an ``enum``, a clear type mismatch. WARNINGS (proceed): unknown
    params (pipelines accept custom/extra ones)."""
    params = params or {}
    specs = {p["name"]: p for p in parse_params(schema)}
    errors: list[str] = []
    warnings: list[str] = []

    for name, spec in specs.items():
        if spec["required"] and name not in _AUTO_PROVIDED and name not in params:
            errors.append(f"missing required param --{name}"
                          + (f" ({spec['help'][:80]})" if spec["help"] else ""))
    for k, v in params.items():
        spec = specs.get(k)
        if spec is None:
            warnings.append(f"unknown param --{k} (not in the pipeline schema)")
            continue
        if spec.get("enum") and str(v) not in [str(e) for e in spec["enum"]]:
            errors.append(f"--{k}={v!r} not allowed; expected one of {spec['enum']}")
        elif not _type_ok(v, spec.get("type")):
            errors.append(f"--{k}={v!r} should be a {spec['type']}")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def latest_release(pipeline: str) -> Optional[str]:
    """The pipeline's latest release tag (GitHub API), for pinning `-r` when the
    caller gave none. None if no releases / not fetchable. Cached."""
    pipeline = (pipeline or "").strip().strip("/")
    if "/" not in pipeline:
        return None
    if pipeline in _RELEASE_CACHE:
        return _RELEASE_CACHE[pipeline]
    d = _get(f"https://api.github.com/repos/{pipeline}/releases/latest")
    tag = d.get("tag_name") if isinstance(d, dict) else None
    _RELEASE_CACHE[pipeline] = tag
    return tag
