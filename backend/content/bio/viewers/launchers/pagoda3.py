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
import os
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

# viewer@0.1 prep goes through pagoda3's own prep.ts (WASM, which delegates to
# lstar's extendForViewer). Chosen over Python lstar.extend_for_viewer because
# (a) WASM has no OpenMP → sidesteps the dup-libomp SIGSEGV, and (b) it's the
# canonical path pagoda3 ships, so the store is written exactly as the viewer
# expects. On by default (ABA_PAGODA3_PREP=0 to disable); best-effort — any
# failure leaves the clean un-prepped store (opens, minus the optimization).
# prep.ts is TypeScript, so it needs node >= 22 (env node may be older); if none
# is found, prep is skipped and we serve the un-prepped store.
_PREP_ENABLED = os.getenv("ABA_PAGODA3_PREP", "1").lower() in ("1", "true", "yes", "on")
LAUNCHER_VERSION = _launcher_version() + ("+prep3" if _PREP_ENABLED else "")
_STORE_SUFFIX = ".lstar.zarr"
_ZIP_SUFFIX = ".lstar.zarr.zip"


def _node_bin() -> "str | None":
    """A node >= 22 that can run pagoda3's TS prep (the conda env's node may be 20)."""
    import shutil as _sh, subprocess as _sp
    for cand in (os.getenv("ABA_NODE_BIN"), "/opt/homebrew/bin/node", "/usr/local/bin/node", _sh.which("node")):
        if not cand or not os.path.exists(cand):
            continue
        try:
            v = _sp.run([cand, "-v"], capture_output=True, text=True, timeout=5).stdout.strip()
            if int(v.lstrip("v").split(".")[0]) >= 22:
                return cand
        except Exception:  # noqa: BLE001
            continue
    return None


def _prep_script() -> Path:
    dist = Path(os.getenv("ABA_PAGODA3_DIST") or (Path.home() / "pagoda" / "pagoda3" / "web" / "dist"))
    # .resolve() is load-bearing: ABA_PAGODA3_DIST may be a frozen dist whose
    # `prep/` is a SYMLINK to the real checkout. prep.ts guards its main-run with
    # `fileURLToPath(import.meta.url) === resolve(argv[1])`; node resolves
    # import.meta.url to the REAL path, so we must invoke it by the real path too
    # (else argv[1] is the symlink, the guard fails, and prep silently no-ops).
    return (dist.parent.parent / "prep" / "prep.ts").resolve()   # <pagoda3>/prep/prep.ts


# The grouping the viewer opens on + computes markers/stats for. Prefer a real
# clustering / cell-type label; avoid boolean QC flags (e.g. `qc_kept`) which make
# a useless 2-group default. extendForViewer requires an explicit grouping (lstar
# doesn't auto-detect), so we always pick the best available.
_GROUPING_PREFER = ("leiden", "louvain", "cluster", "celltype", "cell_type",
                    "cell.type", "annotation", "seurat_clusters", "kmeans", "phenograph")
_GROUPING_AVOID = ("qc_kept", "kept", "highly_variable", "predicted_doublet",
                   "doublet", "outlier", "passed", "is_", "_mt", "_ribo", "sample", "batch")


def _detect_grouping(store: Path) -> "str | None":
    """Pick the best categorical/utf8 per-cell label to open the viewer on.
    Prefers clustering/cell-type names, then any non-QC categorical, and only as a
    last resort a QC/boolean flag (so prep still runs)."""
    try:
        import lstar
        ds = lstar.read(str(store))
    except Exception:  # noqa: BLE001
        return None
    cands: list[str] = []
    for f in ds.fields:
        try:
            fl = ds.field(f)
        except Exception:  # noqa: BLE001
            continue
        if (fl.role == "label" and fl.encoding in ("categorical", "utf8")
                and (fl.span or []) == ["cells"] and fl.subtype != "color"):
            cands.append(f)
    if not cands:
        return None
    # 1) a preferred clustering / cell-type name
    for pref in _GROUPING_PREFER:
        for c in cands:
            if pref in c.lower():
                return c
    # 2) any categorical that isn't an obvious QC/boolean flag
    for c in cands:
        if not any(a in c.lower() for a in _GROUPING_AVOID):
            return c
    # 3) last resort: whatever exists, so prep still produces viewer@0.1
    return cands[0]


def _try_viewer_prep(store: Path) -> None:
    """Best-effort viewer@0.1 via pagoda3's prep.ts. Preps a COPY and swaps on
    success, so a failed/partial prep never leaves a broken served store. Prefers
    raw counts; if the store has none (e.g. a processed .h5ad with only scaled/
    lognorm matrices) falls back to basis=lognorm so it's still optimized
    (approximately) rather than showing pagoda3's 'not viewer-optimized' banner.
    Both failing → keep the clean un-prepped store."""
    import subprocess
    node, script = _node_bin(), _prep_script()
    grouping = _detect_grouping(store)
    if not node or not script.exists() or not grouping:
        return
    prepped = store.parent / (store.name + ".prep")
    for extra in ([], ["basis=lognorm"]):     # raw counts first; then approximate from lognorm
        shutil.rmtree(prepped, ignore_errors=True)
        try:
            shutil.copytree(store, prepped)
            r = subprocess.run([node, str(script), str(prepped), grouping, *extra],
                               capture_output=True, timeout=1800)
        except Exception:  # noqa: BLE001
            shutil.rmtree(prepped, ignore_errors=True)
            return
        if r.returncode == 0:                 # swap the optimized store in
            shutil.rmtree(store, ignore_errors=True)
            prepped.rename(store)
            return
    shutil.rmtree(prepped, ignore_errors=True)  # both failed → keep clean un-prepped


def _rscript() -> "str | None":
    """The Rscript lstar's R bridge uses for `.rds` (Seurat / SCE / pagoda2 /
    conos) — must be an R that has the lstar R package installed. Honors
    `$LSTAR_RSCRIPT`, else a system Rscript (the backend's own PATH may not include
    it, so we resolve explicitly)."""
    import shutil as _sh
    for cand in (os.getenv("LSTAR_RSCRIPT"), "/usr/local/bin/Rscript",
                 "/opt/homebrew/bin/Rscript", _sh.which("Rscript")):
        if cand and os.path.exists(cand):
            return cand
    return None


def _convert_any(src: Path, out: Path) -> None:
    """Convert any lstar-supported source into a `.lstar.zarr` directory store via
    the lstar CLI — ONE entry point for `.h5ad` / `.h5mu` (Python) and, when R +
    the lstar R package are present, Seurat / SingleCellExperiment / pagoda2 /
    conos `.rds` (lstar bridges to Rscript). `--to store` forces store output
    regardless of the temp path's `.building` suffix (the CLI detects format by
    extension). Then best-effort viewer@0.1 via prep.ts (WASM — no OpenMP)."""
    import subprocess
    import sys
    env = {**os.environ}
    rs = _rscript()
    if rs and not env.get("LSTAR_RSCRIPT"):
        env["LSTAR_RSCRIPT"] = rs      # point lstar's .rds bridge at an R with the lstar pkg
    r = subprocess.run(
        [sys.executable, "-m", "lstar", "convert", str(src), str(out), "--to", "store"],
        capture_output=True, text=True, timeout=1800, env=env,
    )
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-600:]
        raise RuntimeError(
            f"lstar convert failed for {src.name!r} (exit {r.returncode}): {tail}")
    if _PREP_ENABLED:
        _try_viewer_prep(out)   # best-effort viewer@0.1 via pagoda3 prep.ts (WASM)


def _pack_download(store_dir: "str | Path", dest: "str | Path") -> None:
    """Pack the directory store into lstar's canonical single-file STORED
    `.lstar.zarr.zip` — produced BY lstar (its exact format: STORED, metadata
    first, range-readable) so a downloaded archive re-opens identically in
    pagoda3 / lstar. Falls back to the equivalent generic STORED pack only if this
    lstar build lacks the packer. (View + the internal cache stay the directory
    `.lstar.zarr` — faster to load, updatable; the zip is for download/transport.)"""
    try:
        from lstar.zarr_io import _pack_stored_zip
    except Exception:  # noqa: BLE001 — older lstar without the single-file packer
        from core.viewers.store_serve import zip_store_stored
        zip_store_stored(Path(store_dir), Path(dest))
        return
    _pack_stored_zip(str(store_dir), str(dest))


def _copy_store(src: Path, out: Path) -> None:
    """Native store already a directory — copy the tree into the served cache."""
    shutil.copytree(src, out)


def _unzip_store(src: Path, out: Path) -> None:
    """Native store shipped as a .lstar.zarr.zip — extract into a directory the
    store route can serve (the browser can't range-read a zip over HTTP). The
    archive's root IS the store root (.zattrs/axes/fields at top level)."""
    import zipfile
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        z.extractall(out)


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
    name = src.name.lower()
    # Pick the derivation by source kind, and strip the (possibly two-part) suffix
    # for a clean output name.
    if name.endswith(_ZIP_SUFFIX):
        convert, suffix = _unzip_store, _ZIP_SUFFIX      # native store (zipped)
    elif name.endswith(_STORE_SUFFIX):
        convert, suffix = _copy_store, _STORE_SUFFIX     # native store (directory)
    else:
        convert, suffix = _convert_any, None             # .h5ad / .h5mu / .rds → convert (lstar CLI)
    stem = src.name[:-len(suffix)] if suffix else src.stem
    tag = hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]
    out_name = f"{stem}-{tag}{_STORE_SUFFIX}"

    store = ensure_derived(src, cache_dir, out_name, LAUNCHER_VERSION, convert)

    return LaunchResult(
        url=f"/pagoda3/?store=/pagoda3-store/{pid}/{store.name}/",
        label="Explore in pagoda3",
        # Origin-shared with the pagoda3 window → its copilot proxies through ABA.
        set_local_storage={"p3-agent-proxy": "/pagoda3-api"},
        # The prepared .lstar.zarr on disk — the download endpoint packs THIS
        # (cache-shared with viewing) into lstar's single-file STORED .lstar.zarr.zip.
        store_path=str(store),
        download_packer=_pack_download,
    )


register_launcher("pagoda3_launcher", launch)
