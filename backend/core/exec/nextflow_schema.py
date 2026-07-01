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
import os
import re
import urllib.request
from typing import Optional

_TIMEOUT = 12
# In-process caches (the tool call runs in the backend process; the head job on the
# compute node never re-validates — validation is pre-submit).
_SCHEMA_CACHE: dict[tuple, Optional[dict]] = {}
_INPUT_SCHEMA_CACHE: dict[tuple, Optional[dict]] = {}
_RELEASE_CACHE: dict[str, Optional[str]] = {}
_RELEASES_CACHE: dict[str, list] = {}
_MINNF_CACHE: dict[tuple, Optional[str]] = {}
_COMPAT_CACHE: dict[tuple, dict] = {}

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


def fetch_input_schema(pipeline: str, revision: Optional[str] = None) -> Optional[dict]:
    """Fetch a pipeline's ``assets/schema_input.json`` — the per-row SAMPLESHEET
    schema (the machine-readable INPUT format), distinct from nextflow_schema.json
    (the run params). Returns the parsed dict or None. Cached per (pipeline, revision).
    This is what lets Guide build a correct ``--input`` file from the user's data."""
    pipeline = (pipeline or "").strip().strip("/")
    if "/" not in pipeline:
        return None
    key = (pipeline, revision)
    if key in _INPUT_SCHEMA_CACHE:
        return _INPUT_SCHEMA_CACHE[key]
    schema = None
    for ref in [r for r in (revision, "master", "main") if r]:
        url = f"https://raw.githubusercontent.com/{pipeline}/{ref}/assets/schema_input.json"
        d = _get(url)
        if isinstance(d, dict) and (d.get("items") or d.get("properties")):
            schema = d
            break
    _INPUT_SCHEMA_CACHE[key] = schema
    return schema


def parse_input_columns(input_schema: dict) -> list[dict]:
    """Parse ``schema_input.json`` into the samplesheet columns, in order:
    [{name, required, type, format, enum, help}]. The samplesheet is an array of
    row objects, so the columns live under ``items.properties`` (+ ``items.required``);
    fall back to top-level ``properties`` for non-standard schemas."""
    items = input_schema.get("items") if isinstance(input_schema, dict) else None
    if not isinstance(items, dict) or not items.get("properties"):
        items = input_schema if isinstance(input_schema, dict) else {}
    props = items.get("properties") or {}
    required = set(items.get("required") or [])
    cols: list[dict] = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        cols.append({
            "name": name,
            "required": name in required,
            "type": spec.get("type"),
            "format": spec.get("format"),
            "enum": spec.get("enum"),
            # nf-core puts the human-readable column help in `errorMessage`.
            "help": (spec.get("description") or spec.get("errorMessage") or "").strip(),
        })
    return cols


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


def param_form(schema: dict, exclude: set | None = None) -> list[dict]:
    """The renderable launch-form spec: ordered groups, each with its params —
    [{group, params:[{name,type,required,default,enum,help}]}]. `exclude` drops
    params ABA sets itself (e.g. `outdir`) so they don't show as misleading empty
    required fields in the launch form."""
    exclude = exclude or set()
    groups: dict[str, list] = {}
    for p in parse_params(schema):
        if p["name"] in exclude:
            continue
        groups.setdefault(p["group"], []).append(
            {k: p.get(k) for k in ("name", "type", "required", "default", "enum", "help")})
    return [{"group": g, "params": ps} for g, ps in groups.items()]


def enrich_plan_steps(steps: list) -> list:
    """For each plan step that launches a pipeline (``skill == 'run_nextflow'`` or
    ``parameters.pipeline`` set), attach a schema-derived ``param_form`` + the
    ``pipeline``/``revision``/``prefilled`` values, so the plan card can render an
    editable launch form inline. Pure + best-effort: a non-pipeline step, or one
    whose schema can't be fetched, is returned unchanged. Used by guide.py's
    present_plan handler to enrich the emitted plan before it reaches the UI."""
    out = []
    for s in steps or []:
        s = dict(s) if isinstance(s, dict) else s
        if not isinstance(s, dict):
            out.append(s); continue
        params = s.get("parameters") or {}
        pipeline = params.get("pipeline") if isinstance(params, dict) else None
        if ((s.get("skill") == "run_nextflow" or pipeline) and pipeline):
            revision = params.get("revision")
            schema = fetch_schema(pipeline, revision)
            if schema:
                s["pipeline"] = pipeline
                s["revision"] = revision
                prefilled = params.get("params") or {}
                s["prefilled"] = prefilled
                # The `test` profile ships its own input data, so `input` is auto-provided just
                # like `outdir` — showing it as an empty required field misleads users into
                # thinking they must supply a samplesheet. Hide it for a test run UNLESS the plan
                # actually prefilled an input path (then it's a real, editable choice).
                excl = set(_AUTO_PROVIDED)
                profile = params.get("profile")
                if (profile and any(t.strip() == "test" for t in str(profile).split(","))
                        and not prefilled.get("input")):
                    excl.add("input")
                s["param_form"] = param_form(schema, exclude=excl)
        out.append(s)
    return out


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


def pipeline_doc_links(pipeline: str, revision: Optional[str] = None) -> dict:
    """Canonical documentation locations for a pipeline — fetchable URLs the agent can
    read ON DEMAND when it hits doubt (how to format the input, what an output means,
    an unusual param). We don't parse these; we just point at them. nf-core pipelines
    follow a fixed layout; for any repo we still give the README + repo. Version-pinned
    to `revision` when given. (We don't pre-verify each URL — the agent fetches on need.)"""
    pipeline = (pipeline or "").strip().strip("/")
    if "/" not in pipeline:
        return {}
    ref = revision or "master"
    raw = f"https://raw.githubusercontent.com/{pipeline}/{ref}"
    links = {
        "repo": f"https://github.com/{pipeline}",
        "readme": f"{raw}/README.md",
        "usage": f"{raw}/docs/usage.md",        # how to prepare the input + run
        "output": f"{raw}/docs/output.md",      # what the results / files mean
    }
    owner, name = pipeline.split("/", 1)
    if owner == "nf-core":
        links["homepage"] = f"https://nf-co.re/{name}"                      # human overview
        links["parameters"] = f"https://nf-co.re/{name}/{revision or 'latest'}/parameters/"
    return links


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


# ── Nextflow-version compatibility guard ──────────────────────────────────────
# The latest nf-core release often requires a newer Nextflow than a deployment has
# installed (e.g. rnaseq 3.26.0 needs Nextflow ≥25.04.3; the cluster module is 24.10.6).
# Running it fails at startup. So when we INFER a revision, pick the latest release whose
# manifest.nextflowVersion is satisfied by the installed Nextflow — a durable guard that
# self-adjusts as Nextflow is upgraded (this problem recurs whenever the installed version
# trails the newest releases, regardless of deployment shape).

def _ver_tuple(v: Optional[str]) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def installed_nextflow_version() -> Optional[str]:
    """The Nextflow version pipelines run on here — from ABA_NEXTFLOW_MODULE
    ('nextflow/24.10.6' → '24.10.6'), else `nextflow -version`, else None (unknown → don't
    constrain revision inference)."""
    m = re.search(r"(\d+\.\d+\.\d+)", os.environ.get("ABA_NEXTFLOW_MODULE") or "")
    if m:
        return m.group(1)
    try:
        import shutil, subprocess
        if shutil.which("nextflow"):
            out = subprocess.run(["nextflow", "-version"], capture_output=True, text=True, timeout=20).stdout
            mm = re.search(r"version\s+(\d+\.\d+\.\d+)", out)
            return mm.group(1) if mm else None
    except Exception:  # noqa: BLE001
        pass
    return None


def release_min_nextflow(pipeline: str, revision: str) -> Optional[str]:
    """The minimum Nextflow a pipeline release requires — its `manifest.nextflowVersion`
    (e.g. '!>=25.04.3' → '25.04.3'). None if not declared / not fetchable. Cached."""
    key = (pipeline, revision)
    if key in _MINNF_CACHE:
        return _MINNF_CACHE[key]
    txt = _get(f"https://raw.githubusercontent.com/{pipeline}/{revision}/nextflow.config", as_json=False)
    minv = None
    if txt:
        m = re.search(r"nextflowVersion\s*=\s*['\"][^'\"]*?(\d+\.\d+\.\d+)", txt)
        minv = m.group(1) if m else None
    _MINNF_CACHE[key] = minv
    return minv


def _list_releases(pipeline: str) -> list:
    """Release tags newest-first (GitHub API). Cached."""
    if pipeline in _RELEASES_CACHE:
        return _RELEASES_CACHE[pipeline]
    d = _get(f"https://api.github.com/repos/{pipeline}/releases?per_page=30")
    tags = [r.get("tag_name") for r in d if isinstance(r, dict) and r.get("tag_name")] if isinstance(d, list) else []
    _RELEASES_CACHE[pipeline] = tags
    return tags


def latest_compatible_release(pipeline: str, installed: Optional[str] = None,
                              max_check: int = 20) -> dict:
    """The latest release that RUNS on the installed Nextflow (manifest.nextflowVersion ≤
    installed). Returns {revision, latest, installed, min_required, note}: `note` is set when
    the pick differs from the absolute latest (or nothing compatible was found). If the
    installed version is unknown → the absolute latest (no constraint). Cached."""
    pipeline = (pipeline or "").strip().strip("/")
    installed = installed or installed_nextflow_version()
    latest = latest_release(pipeline)
    if "/" not in pipeline or not installed or not latest:
        return {"revision": latest, "latest": latest, "installed": installed,
                "min_required": None, "note": None}
    key = (pipeline, installed)
    if key in _COMPAT_CACHE:
        return _COMPAT_CACHE[key]
    inst_t = _ver_tuple(installed)
    tags = _list_releases(pipeline) or [latest]
    out = None
    for tag in tags[:max_check]:
        minv = release_min_nextflow(pipeline, tag)
        if not minv or _ver_tuple(minv) <= inst_t:
            note = None if tag == latest else (
                f"pinned {tag}: the latest release {latest} requires Nextflow "
                f"≥{release_min_nextflow(pipeline, latest)} but this deployment runs {installed}")
            out = {"revision": tag, "latest": latest, "installed": installed,
                   "min_required": minv, "note": note}
            break
    if out is None:                       # nothing compatible in the window — best-effort latest + warn
        out = {"revision": latest, "latest": latest, "installed": installed,
               "min_required": release_min_nextflow(pipeline, latest),
               "note": (f"no release compatible with Nextflow {installed} in the latest "
                        f"{min(len(tags), max_check)} — using {latest} (may fail at startup); "
                        f"upgrade Nextflow to run newer releases")}
    _COMPAT_CACHE[key] = out
    return out
