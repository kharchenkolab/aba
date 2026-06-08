"""I3 — Compatibility report.

After a recover_project walk completes, scan the (now-imported) project for
references it makes to host-side things — entity types, recipes / skills,
capabilities, tools — and report which ones don't exist on this host.

The report is best-effort. Each registry lookup is wrapped in try/except;
when a registry can't be reached we mark that category as "unknown" rather
than fabricating a missing list.

The full report is written to <project_dir>/recovery_report.json so the UI
banner (I5) and downstream tooling can re-read it cheaply.
"""
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CompatibilityReport:
    pid: str
    source_aba_commit: Optional[str] = None
    source_aba_version: Optional[str] = None
    host_aba_commit: Optional[str] = None
    host_aba_version: Optional[str] = None
    missing_entity_types: list[str] = field(default_factory=list)
    missing_recipes: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    referenced_entity_types: list[str] = field(default_factory=list)
    referenced_recipes: list[str] = field(default_factory=list)
    referenced_capabilities: list[str] = field(default_factory=list)
    referenced_tools: list[str] = field(default_factory=list)
    registries_unknown: list[str] = field(default_factory=list)
    artifacts_missing: int = 0
    artifacts_present: int = 0

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "source": {"aba_commit": self.source_aba_commit, "aba_version": self.source_aba_version},
            "host": {"aba_commit": self.host_aba_commit, "aba_version": self.host_aba_version},
            "missing": {
                "entity_types": list(self.missing_entity_types),
                "recipes": list(self.missing_recipes),
                "capabilities": list(self.missing_capabilities),
                "tools": list(self.missing_tools),
            },
            "referenced": {
                "entity_types": sorted(self.referenced_entity_types),
                "recipes": sorted(self.referenced_recipes),
                "capabilities": sorted(self.referenced_capabilities),
                "tools": sorted(self.referenced_tools),
            },
            "registries_unknown": list(self.registries_unknown),
            "artifacts": {"present": self.artifacts_present, "missing": self.artifacts_missing},
        }


# ─── reference scanners ─────────────────────────────────────────────────────
_MD_RECIPE_KEYS = ("skill", "recipe", "recipes", "skills")
_MD_CAPABILITY_KEYS = ("capability", "capabilities")


def _collect_from_metadata(md: dict, recipes: set[str], caps: set[str]) -> None:
    """Pull recipe / capability references out of an entity metadata blob.
    Tolerates strings, lists, and nested step dicts (for plan entities)."""
    if not isinstance(md, dict):
        return
    for k in _MD_RECIPE_KEYS:
        v = md.get(k)
        if isinstance(v, str):
            recipes.add(v)
        elif isinstance(v, list):
            recipes.update(x for x in v if isinstance(x, str))
    for k in _MD_CAPABILITY_KEYS:
        v = md.get(k)
        if isinstance(v, str):
            caps.add(v)
        elif isinstance(v, list):
            caps.update(x for x in v if isinstance(x, str))
    # Plan steps: metadata.steps = [{"skill": "..."}, ...]
    steps = md.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                _collect_from_metadata(step, recipes, caps)


def _scan_referenced(db_path: Path) -> tuple[set[str], set[str], set[str], set[str]]:
    """Return (types, recipes, capabilities, tools) referenced by the imported DB."""
    types: set[str] = set()
    recipes: set[str] = set()
    caps: set[str] = set()
    tools: set[str] = set()
    if not db_path.exists():
        return types, recipes, caps, tools
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    try:
        # entity types
        for r in c.execute("SELECT DISTINCT type FROM entities"):
            if r["type"]:
                types.add(r["type"])
        # entity metadata → recipes / capabilities
        for r in c.execute("SELECT metadata FROM entities WHERE metadata IS NOT NULL"):
            md_raw = r["metadata"]
            try:
                md = json.loads(md_raw) if isinstance(md_raw, str) else md_raw
            except Exception:
                continue
            _collect_from_metadata(md if isinstance(md, dict) else {}, recipes, caps)
        # tools from exec records
        for r in c.execute("SELECT DISTINCT tool_name FROM execution_records"):
            if r["tool_name"]:
                tools.add(r["tool_name"])
    finally:
        c.close()
    return types, recipes, caps, tools


# ─── host registry lookups (all soft) ───────────────────────────────────────
def _host_entity_types() -> Optional[set[str]]:
    """Live entity-type registry. If empty (content/bio hasn't been imported
    in this process), lazy-load bio's types directly — the recovery walker
    can be invoked from CLI tools that don't boot the full backend."""
    try:
        from core.entity_types.registry import list_type_names, load_types  # noqa: PLC0415
        names = set(list_type_names(include_hidden=True))
        if not names:
            # Try to populate from bio's content pack.
            from pathlib import Path as _P
            bio_types = _P(__file__).resolve().parents[2] / "content/bio/entity_types"
            if bio_types.is_dir():
                load_types(bio_types)
            names = set(list_type_names(include_hidden=True))
        return names or None
    except Exception:
        return None


def _host_recipes() -> Optional[set[str]]:
    try:
        from core.skills.loader import list_skills  # noqa: PLC0415
        return {s.name for s in list_skills()}
    except Exception:
        return None


def _host_capabilities() -> Optional[set[str]]:
    try:
        from core.catalog.catalog import list_capabilities  # noqa: PLC0415
        return {c.get("id") or c.get("name") for c in list_capabilities()}
    except Exception:
        return None


def _host_tools() -> Optional[set[str]]:
    """Enumerate the host's tool catalog. TOOL_SCHEMAS is only populated once
    the MCP gateway boots; we fall back to scanning aba_core's per-cluster
    modules so the report stays meaningful even without a live agent."""
    names: set[str] = set()
    try:
        from content.bio.tools import TOOL_SCHEMAS   # noqa: PLC0415
        names.update(t["name"] for t in TOOL_SCHEMAS if isinstance(t, dict) and t.get("name"))
    except Exception:
        pass
    # Fall back / augment by inspecting aba_core's tool modules — every
    # @mcp.tool() declaration corresponds to one agent-visible tool.
    try:
        from pathlib import Path as _P
        tools_dir = _P(__file__).resolve().parents[2] / "content/bio/mcp_servers/aba_core/tools"
        import re
        for f in tools_dir.glob("*.py"):
            for line in f.read_text().splitlines():
                m = re.match(r"\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line)
                if m and not m.group(1).startswith("_"):
                    names.add(m.group(1))
    except Exception:
        pass
    # The workhorse run_python / run_r live elsewhere — add them by name so the
    # report doesn't false-positive when we can't see the live catalog.
    names.update({"run_python", "run_r"})
    return names or None


# ─── artifact reachability ──────────────────────────────────────────────────
def _check_artifacts(db_path: Path, project_dir: Path) -> tuple[int, int]:
    if not db_path.exists():
        return 0, 0
    present = missing = 0
    c = sqlite3.connect(db_path)
    try:
        for (ap,) in c.execute("SELECT artifact_path FROM entities WHERE artifact_path IS NOT NULL"):
            p = Path(ap)
            if p.exists():
                present += 1
            else:
                missing += 1
    finally:
        c.close()
    return present, missing


# ─── main entrypoint ────────────────────────────────────────────────────────
def build_report(project_dir: Path, *, pid: str, db_path: Optional[Path] = None) -> CompatibilityReport:
    """Build a CompatibilityReport for an imported project and write
    <project_dir>/recovery_report.json. Safe to re-run; idempotent."""
    project_dir = Path(project_dir)
    db = db_path or (project_dir / "project.db")

    rep = CompatibilityReport(pid=pid)

    # Source fingerprint from project.json
    pj_path = project_dir / "project.json"
    if pj_path.exists():
        try:
            pj = json.loads(pj_path.read_text())
            rep.source_aba_commit = pj.get("aba_commit")
            rep.source_aba_version = pj.get("aba_version")
        except Exception:
            pass

    # Host fingerprint
    try:
        from core.recovery.scribe import _aba_fingerprint  # noqa: PLC0415
        hc, hv = _aba_fingerprint()
        rep.host_aba_commit = hc
        rep.host_aba_version = hv
    except Exception:
        pass

    # Referenced
    types, recipes, caps, tools = _scan_referenced(db)
    rep.referenced_entity_types = list(types)
    rep.referenced_recipes = list(recipes)
    rep.referenced_capabilities = list(caps)
    rep.referenced_tools = list(tools)

    # Host registries
    ht = _host_entity_types()
    if ht is None:
        rep.registries_unknown.append("entity_types")
    else:
        rep.missing_entity_types = sorted(types - ht)
    hr = _host_recipes()
    if hr is None:
        rep.registries_unknown.append("recipes")
    else:
        rep.missing_recipes = sorted(recipes - hr)
    hc = _host_capabilities()
    if hc is None:
        rep.registries_unknown.append("capabilities")
    else:
        rep.missing_capabilities = sorted(caps - hc)
    hto = _host_tools()
    if hto is None:
        rep.registries_unknown.append("tools")
    else:
        rep.missing_tools = sorted(tools - hto)

    # Artifact reachability
    pres, miss = _check_artifacts(db, project_dir)
    rep.artifacts_present = pres
    rep.artifacts_missing = miss

    # Persist
    try:
        (project_dir / "recovery_report.json").write_text(json.dumps(rep.to_dict(), indent=2))
    except Exception:
        pass

    return rep
