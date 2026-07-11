"""Best-effort INPUT detection for exec records (provenance §3.2).

An exec record's `inputs[]` records what a run *used*, by identity — never file
bytes. Two cheap signals, unioned:
  1. the focused entity at run time (already the `used`-edge subject), and
  2. any registered dataset whose file path / basename / id appears in the code.

Both are identity-level: an entity list scan + substring matching. No file reads,
no hashing on the hot path (a large-file content hash happens only on an explicit
export/verify — provenance.md §3.2). Shared by the interactive path
(`content/bio/tools/run_exec._write_exec_record`) and the background/job path
(`core/jobs/runner._write_exec_record_for_job`) so both record the same shape.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.graph._schema import WORKSPACE_ID

# Common RNG-seed calls. We RECORD a seed the code already set (descriptive), we do
# not inject one on the interactive path (that would silently change user results —
# injection is a background-executor concern, see core/exec/run.py).
_SEED_RE = re.compile(
    r"(?:np\.random\.seed|numpy\.random\.seed|random\.seed|set\.seed|"
    r"torch\.manual_seed|manual_seed|tf\.random\.set_seed)\s*\(\s*(\d+)\s*\)"
)


def detect_seed(code: str | None) -> int | None:
    """The RNG seed the code set, if any (first match). None when unseeded."""
    if not code:
        return None
    m = _SEED_RE.search(code)
    return int(m.group(1)) if m else None


def resolve_inputs(code: str | None, focus_entity_id: str | None = None) -> list[dict]:
    """Input list for an exec record: `[{ref, kind, name?, path?}]`, deduped by ref.

    The focused entity (any type) plus every registered dataset the code references
    by its `artifact_path`, that path's basename, or the dataset entity id. Matching
    on a file basename (which carries an extension) keeps false positives low; we
    never match on a dataset's free-text title. Never raises — provenance is
    best-effort and must not block a run.
    """
    inputs: list[dict] = []
    seen: set[str] = set()

    def _add(ref: str | None, kind: str, name: str | None = None, path: str | None = None) -> None:
        if not ref or ref in seen:
            return
        seen.add(ref)
        item: dict = {"ref": ref, "kind": kind}
        if name:
            item["name"] = name
        if path:
            item["path"] = path
        inputs.append(item)

    try:
        from core.graph.entities import get_entity, list_entities
    except Exception:  # noqa: BLE001
        return inputs

    # 1. The focused entity (its own type — a dataset focus reads as kind:dataset).
    if focus_entity_id and focus_entity_id != WORKSPACE_ID:
        try:
            fe = get_entity(focus_entity_id)
            if fe:
                _add(focus_entity_id, fe.get("type") or "entity", fe.get("title"))
        except Exception:  # noqa: BLE001
            pass

    # 2. Registered datasets referenced in the code.
    if code:
        try:
            for e in list_entities(include_archived=False):
                if e.get("type") != "dataset":
                    continue
                p = (e.get("artifact_path") or "").strip()
                base = Path(p).name if p else ""
                did = e.get("id") or ""
                if (p and p in code) or (base and base in code) or (did and did in code):
                    _add(did, "dataset", e.get("title"), p or None)
        except Exception:  # noqa: BLE001
            pass

    return inputs
