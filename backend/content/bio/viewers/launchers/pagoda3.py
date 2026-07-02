"""pagoda3 external-viewer launcher (misc/pagoda3_integration.md B1/B3).

Turns a project's single-cell file into a pagoda3 launch URL:
  - `.lstar.zarr` (native)  → cached copy under the project's pagoda3/ dir so
                              it's reachable via /pagoda3-store (and passes the
                              store route's symlink-safe containment check)
  - `.h5ad` (and friends)   → converted to `.lstar.zarr` via lstar, cached
pagoda3 reads the store over HTTP Range; since it shares ABA's origin it picks
up `p3-agent-proxy=/pagoda3-api`, so its copilot rides ABA's credential.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from core.viewers.launchers import register_launcher, LaunchResult
from core.viewers.convert_cache import ensure_derived

# Cache version = the installed lstar-sc version, so upgrading it (e.g. 0.1.0 →
# 0.1.1, which changes the store layout / adds the viewer@0.1 profile) AUTOMATICALLY
# re-derives every cached store — no manual bump needed. Suffix `+N` here only if
# THIS launcher's own conversion logic changes independently of lstar.
def _launcher_version() -> str:
    try:
        import importlib.metadata as _md
        return "lstar-sc/" + _md.version("lstar-sc")
    except Exception:  # noqa: BLE001
        return "lstar-sc/unknown"

LAUNCHER_VERSION = _launcher_version()
_STORE_SUFFIX = ".lstar.zarr"


def _convert_h5ad(src: Path, out: Path) -> None:
    import lstar
    lstar.convert_anndata(str(src), str(out))


def _copy_store(src: Path, out: Path) -> None:
    shutil.copytree(src, out)


def _resolve_source(node: dict, pid: str) -> Path:
    """Best-effort resolution of the node to an on-disk file. Prefer the
    entity/tree artifact_path (absolute); fall back to project-relative."""
    from core.config import project_root, project_data_dir
    raw = node.get("artifact_path") or node.get("path") or node.get("name") or ""
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p
    for base in (project_root(pid), project_data_dir(pid), Path.cwd()):
        cand = base / raw
        if cand.exists():
            return cand
    return p            # nonexistent → caller surfaces a clean error


def launch(node: dict, ctx: dict) -> LaunchResult:
    from core.config import project_root, current_project_id
    pid = ctx.get("project_id") or current_project_id()
    src = _resolve_source(node, pid)
    if not src.exists():
        raise FileNotFoundError(
            f"pagoda3: source not found for {node.get('name') or node.get('path')!r}")

    cache_dir = project_root(pid) / "pagoda3"
    stem = src.name[:-len(_STORE_SUFFIX)] if src.name.endswith(_STORE_SUFFIX) else src.stem
    tag = hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]
    out_name = f"{stem}-{tag}{_STORE_SUFFIX}"

    convert = _copy_store if src.name.endswith(_STORE_SUFFIX) else _convert_h5ad
    store = ensure_derived(src, cache_dir, out_name, LAUNCHER_VERSION, convert)

    return LaunchResult(
        url=f"/pagoda3/?store=/pagoda3-store/{pid}/{store.name}/",
        label="Explore in pagoda3",
        # Origin-shared with the pagoda3 window → its copilot proxies through ABA.
        set_local_storage={"p3-agent-proxy": "/pagoda3-api"},
    )


register_launcher("pagoda3_launcher", launch)
