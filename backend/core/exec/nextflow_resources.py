"""nf-core resource estimation — read a pipeline's DECLARED per-process resources to
size a local single-node allocation and decide local-vs-fan-out routing.

A pipeline's `conf/base.config` maps standard nf-core labels (process_single/low/medium/
high/long/high_memory) to {cpus, memory, time}; `check_max(..)` caps each at the profile's
`max_cpus/max_memory/max_time` (the test profile caps them tiny; the default ceiling is high).
The HEAVIEST single task (max cpus / max memory / max time across the labels a pipeline uses,
each capped) drives two decisions:
  • local-allocation sizing — execution="local" runs every task on ONE node, so the
    allocation must fit that heaviest task;
  • routing — if the heaviest task can't fit a sensible single node, the run must fan out.

Fan-out per-task sizing needs nothing here: Nextflow itself reads base.config and sizes each
task job from its label. This module is specifically for the local + routing decisions.
"""
import os
import re
from typing import Optional

from core.exec.nextflow_schema import _get

_RES_CACHE: dict = {}

# nf-core template ceiling for max_* when neither the profile nor nextflow.config sets them.
_TEMPLATE_MAX = {"cpus": 16, "mem_gb": 128.0, "time_h": 240.0}
# Comfortable floor for a local allocation (parallelism headroom for many tiny tasks),
# raised to fit a heavier single task. Overridable; see single_node_ceiling().
_LOCAL_FLOOR = {"cores": 8, "mem_gb": 32}

_MEM_UNIT = {"K": 1e-6, "KB": 1e-6, "M": 1e-3, "MB": 1e-3, "G": 1.0, "GB": 1.0,
             "T": 1000.0, "TB": 1000.0, "P": 1e6, "PB": 1e6}
_TIME_UNIT = {"S": 1 / 3600, "SEC": 1 / 3600, "M": 1 / 60, "MIN": 1 / 60, "H": 1.0, "D": 24.0}


def _f(s) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def mem_to_gb(val: str) -> Optional[float]:
    """'72.GB' / '200.GB' / '6.GB' / \"'128.GB'\" / '12 GB' → GB float."""
    m = re.search(r"([\d.]+)\s*\.?\s*([KMGTP]B?)", (val or "").upper())
    if not m:
        return None
    n = _f(m.group(1).rstrip("."))
    return round(n * _MEM_UNIT.get(m.group(2), 1.0), 3) if n is not None else None


def time_to_h(val: str) -> Optional[float]:
    """'16.h' / '20.h' / '6.h' / '240.h' / '30.min' → hours float."""
    m = re.search(r"([\d.]+)\s*\.?\s*(MIN|SEC|[SMHD])", (val or "").upper())
    if not m:
        return None
    n = _f(m.group(1).rstrip("."))
    return round(n * _TIME_UNIT.get(m.group(2), 1.0), 3) if n is not None else None


def _first_cpus(body: str) -> Optional[float]:
    m = re.search(r"cpus\s*=\s*\{?\s*(?:check_max\()?\s*([\d.]+)", body)
    return _f(m.group(1).rstrip(".")) if m else None


def _first_mem(body: str) -> Optional[float]:
    m = re.search(r"memory\s*=\s*\{?\s*(?:check_max\()?\s*([\d.]+\s*\.?\s*[KMGTP]B?)", body, re.I)
    return mem_to_gb(m.group(1)) if m else None


def _first_time(body: str) -> Optional[float]:
    m = re.search(r"time\s*=\s*\{?\s*(?:check_max\()?\s*([\d.]+\s*\.?\s*(?:min|[smhd]))", body, re.I)
    return time_to_h(m.group(1)) if m else None


def parse_resource_labels(base_config: str) -> dict:
    """Parse conf/base.config → {label: {cpus, mem_gb, time_h}} at attempt=1.
    Includes a 'default' entry from the top-level process{} block; partial labels
    (e.g. process_long sets only time) inherit unspecified fields from 'default'."""
    text = base_config or ""
    # Split on label/name selectors; chunk[0] holds the top-level process{} defaults.
    chunks = re.split(r"with(?:Label|Name)\s*:\s*", text)
    default = {"cpus": _first_cpus(chunks[0]), "mem_gb": _first_mem(chunks[0]),
               "time_h": _first_time(chunks[0])}
    labels: dict = {"default": {k: v for k, v in default.items() if v is not None}}
    for chunk in chunks[1:]:
        m = re.match(r"(\w+)", chunk)
        if not m:
            continue
        name = m.group(1)
        if not name.startswith("process_"):     # skip withName:<PROCESS> overrides + error_* labels
            continue
        body = chunk[: chunk.find("withLabel")] if "withLabel" in chunk else chunk
        row = {"cpus": _first_cpus(body), "mem_gb": _first_mem(body), "time_h": _first_time(body)}
        # inherit unset fields from the default process block
        labels[name] = {k: (row.get(k) if row.get(k) is not None else default.get(k))
                        for k in ("cpus", "mem_gb", "time_h")}
    return labels


def parse_max_caps(config_text: str) -> dict:
    """Parse max_cpus / max_memory / max_time out of a config (test.config or
    nextflow.config). Missing keys are simply absent from the returned dict."""
    text = config_text or ""
    caps: dict = {}
    m = re.search(r"max_cpus\s*=\s*([\d.]+)", text)
    if m:
        caps["cpus"] = _f(m.group(1))
    m = re.search(r"max_memory\s*=\s*'?\s*([\d.]+\s*\.?\s*[KMGTP]B?)", text, re.I)
    if m:
        caps["mem_gb"] = mem_to_gb(m.group(1))
    m = re.search(r"max_time\s*=\s*'?\s*([\d.]+\s*\.?\s*(?:min|[smhd]))", text, re.I)
    if m:
        caps["time_h"] = time_to_h(m.group(1))
    # Newer nf-core (nf-schema era) dropped max_* for a resourceLimits block:
    #   resourceLimits = [ cpus: 4, memory: '15.GB', time: '1.h' ]
    # Fill any caps the max_* keys didn't provide (so a -profile test isn't mis-sized to the
    # 128GB template fallback → wrong local-vs-slurm routing).
    rl = re.search(r"resourceLimits\s*=\s*\[(.*?)\]", text, re.S)
    if rl:
        body = rl.group(1)
        if "cpus" not in caps:
            mm = re.search(r"cpus\s*:\s*([\d.]+)", body)
            if mm:
                caps["cpus"] = _f(mm.group(1))
        if "mem_gb" not in caps:
            mm = re.search(r"memory\s*:\s*['\"]?\s*([\d.]+\s*\.?\s*[KMGTP]B?)", body, re.I)
            if mm:
                caps["mem_gb"] = mem_to_gb(mm.group(1))
        if "time_h" not in caps:
            mm = re.search(r"time\s*:\s*['\"]?\s*([\d.]+\s*\.?\s*(?:min|[smhd]))", body, re.I)
            if mm:
                caps["time_h"] = time_to_h(mm.group(1))
    return caps


def single_node_ceiling() -> dict:
    """Largest single-node allocation local mode will request (also the local-viability
    bound). Overridable for the deploy's node sizes."""
    cores = _f(os.environ.get("ABA_NEXTFLOW_LOCAL_MAX_CORES")) or 36
    mem = _f(os.environ.get("ABA_NEXTFLOW_LOCAL_MAX_MEM_GB")) or 180.0
    return {"cores": int(cores), "mem_gb": float(mem)}


def compute_estimate(labels: dict, caps: dict, ceiling: Optional[dict] = None) -> dict:
    """Pure: from parsed labels + max_* caps, derive the heaviest single task (capped),
    a recommended local allocation (fits the heaviest task, floored for parallelism,
    bounded by the single-node ceiling) and whether local execution is viable."""
    ceiling = ceiling or single_node_ceiling()
    cap_c, cap_m, cap_t = caps.get("cpus"), caps.get("mem_gb"), caps.get("time_h")

    def capped(vals, cap):
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        hi = max(vals)
        return min(hi, cap) if cap is not None else hi

    real = [v for k, v in labels.items()]           # all label rows incl 'default'
    heavy = {
        "cpus": capped([r.get("cpus") for r in real], cap_c),
        "mem_gb": capped([r.get("mem_gb") for r in real], cap_m),
        "time_h": capped([r.get("time_h") for r in real], cap_t),
    }
    h_c = heavy["cpus"] or 1
    h_m = heavy["mem_gb"] or 1.0
    local_viable = (h_c <= ceiling["cores"]) and (h_m <= ceiling["mem_gb"])
    rec = {
        "cores": int(min(max(h_c, _LOCAL_FLOOR["cores"]), ceiling["cores"])),
        "mem_gb": int(min(max(h_m, _LOCAL_FLOOR["mem_gb"]), ceiling["mem_gb"])),
    }
    return {"heaviest_task": heavy, "caps": caps, "ceiling": ceiling,
            "local_viable": local_viable, "recommended_local": rec,
            "reason": (None if local_viable else
                       f"heaviest task needs {h_c} cpu / {h_m:g} GB — exceeds the "
                       f"single-node ceiling ({ceiling['cores']} cpu / {ceiling['mem_gb']:g} GB); "
                       f"use fan-out (execution=slurm)")}


def _profile_has_test(profile: Optional[str]) -> bool:
    return any(t.strip() == "test" for t in (profile or "").split(","))


def estimate_pipeline_resources(pipeline: str, revision: Optional[str] = None,
                                profile: Optional[str] = None) -> Optional[dict]:
    """Fetch a pipeline's conf/base.config (+ the profile's max_* caps) and return
    compute_estimate(...). None on a fetch miss (caller falls back to fixed defaults).
    Cached per (pipeline, revision, profile)."""
    pipeline = (pipeline or "").strip().strip("/")
    if "/" not in pipeline:
        return None
    key = (pipeline, revision, profile)
    if key in _RES_CACHE:
        return _RES_CACHE[key]
    refs = [r for r in (revision, "master", "main") if r]
    base = None
    for ref in refs:
        base = _get(f"https://raw.githubusercontent.com/{pipeline}/{ref}/conf/base.config",
                    as_json=False)
        if base:
            break
    if not base:
        _RES_CACHE[key] = None
        return None
    labels = parse_resource_labels(base)
    caps: dict = {}
    # test profile → conf/test.config caps (tiny); else the pipeline's nextflow.config max_*.
    ref = refs[0] if refs else "master"
    if _profile_has_test(profile):
        tc = _get(f"https://raw.githubusercontent.com/{pipeline}/{ref}/conf/test.config",
                  as_json=False)
        caps = parse_max_caps(tc or "")
    if not caps:
        nc = _get(f"https://raw.githubusercontent.com/{pipeline}/{ref}/nextflow.config",
                  as_json=False)
        caps = parse_max_caps(nc or "") or dict(_TEMPLATE_MAX)
    est = compute_estimate(labels, caps)
    est["pipeline"], est["revision"], est["profile"] = pipeline, revision, profile
    est["labels"] = labels
    _RES_CACHE[key] = est
    return est
