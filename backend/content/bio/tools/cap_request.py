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


def verify_block(req: CapRequest, *, libname: "str | None" = None,
                 import_name: "str | None" = None) -> dict:
    """The request's claim in the substrate's ONE verify grammar
    ({import|loads, versions: '>=X'|'==X'} — weft P0). aba AUTHORS the claim
    (what must hold, from conversation meaning); weft EVALUATES it where the
    artifact lives. `libname`/`import_name` let the lane supply its resolved
    load name; the version floor comes from the request."""
    if (req.language or "python") == "r":
        nm = libname or req.library or req.name
        out: dict = {"loads": [nm]}
    else:
        nm = import_name or req.library or req.name
        out = {"import": [nm]}
    if req.min_version:
        out["versions"] = {nm: f">={req.min_version}"}
    return out


def compile_extend(req: CapRequest, cap: "dict | None",
                   env_language: str) -> "tuple[list, str] | None":
    """Compose the substrate specs + explicit eco for a NAMED-env install —
    the ONE grammar/eco composer for the extend door (env_refi2 stage C).

    The github grammar (`owner/repo[/subdir][@ref]`) is remotes' own — a
    subdir is not exotic (monorepos keep the R package under e.g. R/), and
    flattening it to a bare name silently substituted a same-named registry
    package (D3). Ecosystems are EXPLICIT: the prefix split downstream is a
    compatibility default, never policy (F3) — a conda-provisioned tool must
    not resolve a PyPI namesake. Returns None for non-package capabilities
    (the dispatch's fall-through)."""
    prov = (cap or {}).get("provisioning") or {}
    if prov.get("pip"):
        return list(prov["pip"]), "pypi"
    if prov.get("conda"):
        _c = prov["conda"]
        _spec = _c.get("spec") if isinstance(_c, dict) else _c
        specs = [_spec] if isinstance(_spec, str) else list(_spec)
        return specs, "conda"
    is_r_pkg = ((cap or {}).get("archetype") == "r_package" or prov.get("r")
                or (env_language == "r"
                    and (cap is None
                         or (cap or {}).get("archetype") in (None, "library"))))
    if is_r_pkg:
        pkg = req.package or req.name
        if req.source == "github":
            sub = (req.subdir or "").strip("/")
            spec = pkg + (f"/{sub}" if sub else "") \
                   + (f"@{req.ref}" if req.ref else "")
            return [spec], "cran"
        if req.source == "conda":
            return [pkg], "conda"
        if req.source == "bioconductor":
            nm = pkg if pkg.startswith("bioconductor-") \
                else f"bioconductor-{pkg.lower()}"
            return [nm], "conda"
        return [pkg], "cran"
    if cap is None or (cap or {}).get("archetype") in (None, "library"):
        return [req.package or req.name], "pypi"
    return None                     # non-package capability


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
