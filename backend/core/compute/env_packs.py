"""Named env packs → weft EnvIDs (weft rewrite W1, misc/weft_rewrite.md §4b).

The bundle's ``envs/`` facet (core/bundle/loader._compose_envs) is the DATA —
named base EnvSpecs layered system⊂institution⊂lab⊂user. This module is the
thin bridge from that data to the compute substrate: resolve a pack by name,
hand its verbatim ``spec`` to weft ``env_ensure``, get back an EnvID.

Kept in core/compute (not core/exec) so the only weft doorway stays this
package (test_compute_ports guards it) and the module is domain-neutral —
"env pack" is a platform concept; the packs themselves are content.

W1 scope: resolution + ensure + capability import-name lookup. The kernel/run
cutover to these EnvIDs is W2; the publish/adopt base catalog is W3.
"""
from __future__ import annotations

from typing import Optional

from core.compute import adapter as _adapter
from core.compute.errors import ComputeError


def _packs() -> dict[str, dict]:
    """Name → pack doc, from the active EffectiveBundle's envs/ facet."""
    from core.bundle.active import get_bundle
    return {p.name: p.spec for p in get_bundle().env_packs}


# bare on/off are YAML 1.1 booleans; a content author who forgets to quote
# would otherwise get True/False. Coerce back to the policy vocabulary.
_STATE_FROM_BOOL = {True: "on", False: "off"}


def _norm_state(v) -> str:
    if isinstance(v, bool):
        return _STATE_FROM_BOOL[v]
    return str(v) if v else "first_use"


def list_packs() -> list[dict]:
    """Render-ready pack rows for doctor / a Modules UI (weft-independent view;
    the weft realization state is added by env_status when a pack is ensured)."""
    out = []
    for name, doc in sorted(_packs().items()):
        out.append({
            "name": name,
            "title": doc.get("title") or name,
            "languages": doc.get("languages") or [],
            "default_state": _norm_state(doc.get("default_state")),
            "role": doc.get("role") or "base",
        })
    return out


def pack_spec(name: str) -> Optional[dict]:
    """The verbatim weft EnvSpec for a named pack (its ``spec:`` block), or None."""
    doc = _packs().get(name)
    if doc is None:
        return None
    spec = dict(doc.get("spec") or {})
    # weft keys the EnvID off the spec; carry the pack name as the env label
    # so realizations are legible without changing identity.
    spec.setdefault("name", name)
    return spec


async def ensure_pack(name: str, *, update: bool = False) -> dict:
    """Solve/realize a named pack through the compute substrate → its EnvID row.
    Raises ComputeError (substrate offline, unknown pack, solve conflict) — the
    caller surfaces the structured cause; nothing is guessed."""
    spec = pack_spec(name)
    if spec is None:
        raise ComputeError("unknown_pack", f"no env pack named {name!r}",
                            stage="aba",
                            hints={"available": sorted(_packs())})
    return await _adapter.get_compute().env_ensure(spec, update=update)


def import_name_for(capability: str) -> Optional[str]:
    """Resolve a capability to its import name via any pack's ``import_names``
    map (§4b(i)): so ``ensure_capability`` recognizes what a base already
    provides and never routes to an external registry — or a same-named
    unrelated package — for something present. First match across packs wins."""
    for doc in _packs().values():
        mapping = doc.get("import_names")
        if isinstance(mapping, dict) and capability in mapping:
            return str(mapping[capability])
    return None


def packs_providing(import_name: str) -> list[str]:
    """Which packs declare (in deps or import_names) a given import name — used
    to tell the agent 'that's already in base python-bio', not 'not installable'."""
    hits = []
    for name, doc in _packs().items():
        names = set((doc.get("import_names") or {}).values())
        names |= set((doc.get("import_names") or {}).keys())
        deps = (doc.get("spec") or {}).get("deps") or {}
        flat = " ".join(str(x) for v in deps.values() if isinstance(v, list) for x in v)
        if import_name in names or import_name in flat.split():
            hits.append(name)
    return hits
