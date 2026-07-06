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

# viewer@0.1 optimization is done IN-PROCESS by lstar's Python `extend_for_viewer`
# (see `_optimize_store`), NOT pagoda3's prep.ts (WASM). prep.ts needs node >= 22,
# which is unavailable on prod / old-glibc hosts (native node fails to build), so it
# silently skipped there — leaving every conversion with the "Not viewer-optimized"
# banner. The Python path is node-free + deterministic; the dup-libomp SIGSEGV that
# once argued for WASM is fixed in lstar-sc >=0.1.6 (we still run it in a subprocess
# to isolate libomp). Bump the +N suffix when THIS optimization logic changes.
LAUNCHER_VERSION = _launcher_version() + "+viewer2"   # in-process extend_for_viewer (raw->lognorm)
_STORE_SUFFIX = ".lstar.zarr"
_ZIP_SUFFIX = ".lstar.zarr.zip"


def pagoda3_dist_path() -> Path:
    """Where pagoda3's built web bundle lives — the single source of truth for
    every consumer (the `/pagoda3` static mount the app root wires up, and prep
    below). First existing wins:
      1. `$ABA_PAGODA3_DIST`                — explicit override (dev sets this)
      2. `$ABA_HOME/vendor/pagoda3/dist`    — the installer's vendored v0.1.0
                                              release bundle (deploy default)
      3. `~/pagoda/pagoda3/web/dist`        — a dev checkout
    Returns the override / deploy default even if absent, so a caller can report a
    clean 'not present' against the expected location rather than guessing."""
    env = os.getenv("ABA_PAGODA3_DIST")
    if env:
        return Path(env)
    home = Path(os.getenv("ABA_HOME") or (Path.home() / ".aba"))
    for cand in (home / "vendor" / "pagoda3" / "dist",
                 Path.home() / "pagoda" / "pagoda3" / "web" / "dist"):
        if (cand / "index.html").is_file():
            return cand
    return home / "vendor" / "pagoda3" / "dist"


def _rscript() -> "str | None":
    """The Rscript lstar's R bridge uses for `.rds` (Seurat / SCE / pagoda2 /
    conos) — must be an R with the lstar R package installed. Preference:
    `$LSTAR_RSCRIPT` (explicit override), then ABA's tools-env R (it ships the
    domain frameworks — Seurat / SingleCellExperiment / …), then a system Rscript.
    (The backend's own PATH may not include any R, so we resolve explicitly.)"""
    import shutil as _sh
    cands = [os.getenv("LSTAR_RSCRIPT")]
    try:
        from core.config import ENVS_DIR
        cands.append(str(Path(ENVS_DIR) / "tools" / "bin" / "Rscript"))
    except Exception:  # noqa: BLE001
        pass
    cands += ["/usr/local/bin/Rscript", "/opt/homebrew/bin/Rscript", _sh.which("Rscript")]
    for cand in cands:
        if cand and os.path.exists(cand):
            return cand
    return None


# Optimize a store to the `viewer@0.1` profile IN-PROCESS via lstar's Python
# `extend_for_viewer` — the node-free replacement for the old prep.ts (WASM) step.
# Run as a SUBPROCESS (fresh interpreter) to isolate lstar's OpenMP/libomp from any
# libomp already loaded in the backend (the dup-libomp SIGSEGV that first pushed us
# to WASM). Raw counts are preferred; falls back to `basis='lognorm'` when the
# source has no raw counts (e.g. a scaled/log-normalized scanpy `.h5ad`) — EXACTLY
# the raw→lognorm fallback prep.ts had, which the CLI `--viewer` flag can't express.
_OPTIMIZE_SCRIPT = (
    "import sys, os, shutil, lstar\n"
    "store = sys.argv[1]; tmp = store + '.opt.lstar.zarr'\n"
    "shutil.rmtree(tmp, ignore_errors=True)\n"
    "ds = lstar.read(store)\n"
    "try:\n"
    "    lstar.extend_for_viewer(ds)\n"                 # raw counts (preferred)
    "except Exception:\n"
    "    lstar.extend_for_viewer(ds, basis='lognorm')\n"  # approximate from lognorm
    "lstar.write(ds, tmp)\n"                             # tmp ends in .lstar.zarr -> store
    "shutil.rmtree(store); os.rename(tmp, store)\n"      # atomic-ish swap
)


def _optimize_store(store: Path, env: dict, sp) -> None:
    """Best-effort: add viewer@0.1 to `store` in place. On any failure keep the
    functional un-optimized store (viewer works, just recomputes per session)."""
    import subprocess
    import sys
    sp("Optimizing for fast viewing…")
    r = subprocess.run([sys.executable, "-c", _OPTIMIZE_SCRIPT, str(store)],
                       capture_output=True, text=True, timeout=1800, env=env)
    if r.returncode != 0:
        shutil.rmtree(Path(str(store) + ".opt.lstar.zarr"), ignore_errors=True)


def _convert_any(src: Path, out: Path, set_phase=None) -> None:
    """Convert any lstar-supported source into a `.lstar.zarr` directory store via
    the lstar CLI — ONE entry point for `.h5ad` / `.h5mu` (Python) and, when R +
    the lstar R package are present, Seurat / SingleCellExperiment / pagoda2 /
    conos `.rds` (lstar bridges to Rscript). `--to store` forces store output
    regardless of the temp path's `.building` suffix (the CLI detects format by
    extension). Then optimizes the store to `viewer@0.1` IN-PROCESS via
    `extend_for_viewer` (`_optimize_store`) — node-free, so it works where the old
    prep.ts (WASM, needs node ≥22 — unavailable on prod/old-glibc) silently didn't,
    which left every conversion showing the "Not viewer-optimized" banner.
    `set_phase` reports the sub-step to the launch page."""
    import subprocess
    import sys
    sp = set_phase or (lambda *_: None)
    env = {**os.environ}
    rs = _rscript()
    if rs and not env.get("LSTAR_RSCRIPT"):
        env["LSTAR_RSCRIPT"] = rs      # point lstar's .rds bridge at an R with the lstar pkg
    sp(f"Converting {src.name} → viewer store…")
    r = subprocess.run(
        [sys.executable, "-m", "lstar", "convert", str(src), str(out), "--to", "store"],
        capture_output=True, text=True, timeout=1800, env=env,
    )
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-600:]
        raise RuntimeError(
            f"lstar convert failed for {src.name!r} (exit {r.returncode}): {tail}")
    _optimize_store(out, env, sp)   # best-effort viewer@0.1 (in-process, node-free)


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


def _copy_store(src: Path, out: Path, set_phase=None) -> None:
    """Native store already a directory — copy the tree into the served cache."""
    (set_phase or (lambda *_: None))("Copying store…")
    shutil.copytree(src, out)


def _unzip_store(src: Path, out: Path, set_phase=None) -> None:
    """Native store shipped as a .lstar.zarr.zip — extract into a directory the
    store route can serve (the browser can't range-read a zip over HTTP). The
    archive's root IS the store root (.zattrs/axes/fields at top level)."""
    import zipfile
    (set_phase or (lambda *_: None))("Unpacking store…")
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        z.extractall(out)


def _resolve_source(node: dict, pid: str) -> Path:
    """Best-effort resolution of the node to an on-disk file. Prefer the
    entity/tree artifact_path (absolute); fall back to project-relative; finally
    search the project's work dirs by basename.

    The last fallback matters for `.lstar.zarr` **directory** stores written by a
    run: a Run's output is shown at the LOGICAL path `threads/<t>/runs/<r>/output/`
    but physically lives under `work/<ana_id>/` (tree.py). Regular files carry a
    physical `artifact_path`, but a directory store can resolve to the logical
    output path with no physical path — so `project_root/<logical>` doesn't exist.
    The store is really at `work/<ana_id>/<name>`, so scan there (newest first)."""
    from core.config import project_root, project_data_dir
    raw = node.get("artifact_path") or node.get("path") or node.get("name") or ""
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p
    for base in (project_root(pid), project_data_dir(pid), Path.cwd()):
        cand = base / raw
        if cand.exists():
            return cand
    # Fallback: a run wrote the source into its work dir (work/<ana_id>/<name>),
    # which the logical output-tree path doesn't map to. Match by basename.
    name = Path(raw).name
    work = project_root(pid) / "work"
    if name and work.exists():
        matches = sorted(work.glob(f"*/{name}"), key=lambda m: m.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return p            # nonexistent → caller surfaces a clean error


def launch(node: dict, ctx: dict) -> LaunchResult:
    from core.config import project_root
    from core.projects import current_project_id
    pid = ctx.get("project_id") or current_project_id()
    # Reported to the launch page's poller so the user sees which step is running
    # (convert / optimize / unpack) rather than a static spinner. Only fires when
    # ensure_derived actually (re)builds — a cached store returns instantly.
    set_phase = ctx.get("set_phase") or (lambda *_: None)
    src = _resolve_source(node, pid)
    if not src.exists():
        raise FileNotFoundError(
            f"pagoda3: source not found for {node.get('name') or node.get('path')!r}")

    cache_dir = project_root(pid) / "pagoda3"
    name = src.name.lower()
    # Pick the derivation by source kind, and strip the (possibly two-part) suffix
    # for a clean output name.
    if name.endswith(_ZIP_SUFFIX):
        base_convert, suffix = _unzip_store, _ZIP_SUFFIX      # native store (zipped)
    elif name.endswith(_STORE_SUFFIX):
        base_convert, suffix = _copy_store, _STORE_SUFFIX     # native store (directory)
    else:
        base_convert, suffix = _convert_any, None             # .h5ad / .h5mu / .rds → convert (lstar CLI)
    stem = src.name[:-len(suffix)] if suffix else src.stem
    tag = hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]
    out_name = f"{stem}-{tag}{_STORE_SUFFIX}"

    def convert(s: Path, o: Path) -> None:      # bind set_phase into the 2-arg callback
        base_convert(s, o, set_phase)

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
