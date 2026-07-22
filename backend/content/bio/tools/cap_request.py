"""The capability request, normalized ONCE (env_refi2 stage A).

This module is the single place tool arguments and provisioning records are
read and merged into a request object; every provisioning door receives the
object instead of re-plucking raw inputs. The point is FIDELITY (the layer
contract, misc/env_exec_contract.md): a field the agent sent must not
evaporate at a door that never learned about it — `min_version`/`force` were
honored only in the R session lane while every python path and the whole
env= lane silently dropped them, and the env= dispatch flattened a
github/subdir/ref record to a bare name (env_refi2 §1, F1/D3).

Stage A is transport only: fields ARRIVE everywhere; enforcement (verify)
is stage B, grammar composition is stage C. The merge rule here reproduces
the R session lane's exactly — behavior is frozen, ownership moves.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The agent-facing override keys: explicit tool input wins over the
# capability record's provisioning block (the R lane's historical rule).
_OVERRIDE_KEYS = ("ref", "source", "package", "subdir", "library")


@dataclass
class CapRequest:
    name: str
    language: "str | None" = None
    # postcondition (authored claims — enforcement is the verify stage)
    min_version: "str | None" = None
    force: bool = False
    library: "str | None" = None
    # source grammar (merged: explicit input over the record)
    source: str = "cran"
    package: "str | None" = None
    subdir: "str | None" = None
    ref: "str | None" = None
    repos: list = field(default_factory=list)
    conda_packages: list = field(default_factory=list)
    # which override keys the CALLER supplied (some lanes treat an explicit
    # source/ref override as an implicit force — they need provenance of who
    # said what, which a merged value alone cannot carry)
    explicit_overrides: tuple = ()
    # context
    project: str = ""
    ctx: "dict | None" = None


def classify_language(cap: "dict | None") -> "str | None":
    """The capability's language, from its record — ONE owner (M6).

    conda provisioning names an ARTIFACT, not a runtime: a cli/tool
    archetype with a conda spec is language-neutral, not python — the inline
    heuristic classified it python, so `ensure_capability(tool, language='r')`
    took the wrong-ecosystem mismatch reroute for a tool that has no
    language. Only library-ish archetypes imply python.
    """
    if not cap:
        return None
    prov = cap.get("provisioning") or {}
    if cap.get("archetype") == "r_package" or prov.get("r"):
        return "r"
    if prov.get("pip"):
        return "python"
    arch = cap.get("archetype")
    if prov.get("conda"):
        return "python" if arch in (None, "library") else None
    if arch in (None, "library"):
        return "python"
    return None


def _clean(v) -> "str | None":
    s = str(v).strip() if v is not None else ""
    return s or None


def build_cap_request(input_: dict, cap: "dict | None", ctx: "dict | None",
                      *, name: "str | None" = None,
                      language: "str | None" = None,
                      project: "str | None" = None) -> CapRequest:
    """Merge (tool input, capability record, context) → CapRequest.

    `name`/`language` are the entry's RESOLVED values (env-fixed scope,
    inference) when the caller has them; `language=None` stays None —
    inference declines, this constructor never guesses. Empty strings are
    ABSENT, not overrides.
    """
    input_ = input_ or {}
    rp = dict(((cap or {}).get("provisioning") or {}).get("r") or {})
    overridden = []
    for k in _OVERRIDE_KEYS:
        v = _clean(input_.get(k))
        if v is not None:
            rp[k] = v
            overridden.append(k)
    if project is None:
        from core import projects
        project = str(projects.current() or "default")
    return CapRequest(
        name=_clean(name) or _clean(input_.get("name"))
             or _clean(input_.get("capability")) or "",
        language=language,
        min_version=_clean(input_.get("min_version")) or _clean(rp.get("min_version")),
        force=bool(input_.get("force")),
        library=_clean(rp.get("library")),
        source=_clean(rp.get("source")) or "cran",
        package=_clean(rp.get("package")),
        subdir=_clean(rp.get("subdir")),
        ref=_clean(rp.get("ref")),
        repos=list(input_.get("repos") or []),
        conda_packages=list(input_.get("conda_packages") or []),
        explicit_overrides=tuple(overridden),
        project=project,
        ctx=ctx,
    )
