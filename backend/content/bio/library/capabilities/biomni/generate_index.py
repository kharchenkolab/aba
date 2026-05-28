"""Generate the biomni collection index (collections.md).

Reads biomni's upstream `tool/tool_description/*.py` (each a pure
`description = [ {...}, ... ]` literal) and writes a lightweight `index.json`:
one entry per tool function, carrying name, domain (the module), summary,
import path, and params. This index is the searchable knowledge-base and ships
with ABA — discovery works without biomni installed.

Re-run when biomni updates:
    .venv/bin/python backend/content/bio/library/capabilities/biomni/generate_index.py
"""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _backend_dir() -> Path:
    """Walk up to the repo's `backend/` dir (robust to where this file lives)."""
    for p in Path(__file__).resolve().parents:
        if p.name == "backend":
            return p
    raise SystemExit("could not locate the backend/ dir from this script.")


def _biomni_td_dir() -> Path:
    sys.path.insert(0, str(_backend_dir()))
    from core.config import BIOMNI_DIR
    if not BIOMNI_DIR:
        raise SystemExit("BIOMNI_DIR not set / vendored biomni not found.")
    td = Path(BIOMNI_DIR) / "biomni" / "tool" / "tool_description"
    if not td.is_dir():
        raise SystemExit(f"biomni tool_description dir not found at {td}")
    return td


def _load_description(py_file: Path) -> list[dict]:
    """Load a tool_description module's `description` list in isolation (these
    files are pure data — no imports — so a standalone spec load is safe and
    avoids importing the heavy biomni package)."""
    spec = importlib.util.spec_from_file_location(f"_biomni_td_{py_file.stem}", py_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    desc = getattr(mod, "description", None)
    return desc if isinstance(desc, list) else []


def _params(entries) -> list[dict]:
    out = []
    for p in (entries or []):
        if not isinstance(p, dict):
            continue
        out.append({k: p.get(k) for k in ("name", "type", "default", "description")
                    if k in p})
    return out


def build() -> list[dict]:
    td = _biomni_td_dir()
    index: list[dict] = []
    for py in sorted(td.glob("*.py")):
        if py.stem == "__init__":
            continue
        domain = py.stem
        for e in _load_description(py):
            name = (e.get("name") or "").strip()
            if not name:
                continue
            index.append({
                "name": name,
                "domain": domain,
                "summary": " ".join((e.get("description") or "").split()),
                "function": name,
                # Pointer back to the biomni implementation — reference only
                # (we don't import it); used to lift the solution into lakeFS later.
                "source_ref": f"biomni/tool/{domain}.py::{name}",
                "required_params": _params(e.get("required_parameters")),
                "optional_params": _params(e.get("optional_parameters")),
            })
    return index


def main() -> int:
    index = build()
    out = HERE / "index.json"
    out.write_text(json.dumps(index, indent=1))
    domains = sorted({e["domain"] for e in index})
    print(f"wrote {len(index)} capabilities across {len(domains)} domains -> {out}")
    print("domains:", ", ".join(domains))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
