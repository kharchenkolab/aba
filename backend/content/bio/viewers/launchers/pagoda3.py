"""pagoda3 external-viewer launcher (misc/pagoda3_integration.md B1/B3).

Turns a project's single-cell file into a pagoda3 launch URL:
  - `.lstar.zarr` (native)  → symlinked into the project's pagoda3/ dir so it's
                              reachable via /pagoda3-store WITHOUT copying the
                              tree (the store route follows a project-internal
                              link); copied only if it lives outside the project
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

# Cache version = the installed lstar-sc version, so upgrading it (e.g. 0.1.x →
# 0.2.0, which switched the on-disk store to zarr v3) AUTOMATICALLY
# re-derives every cached store — no manual bump needed. Suffix `+N` here only if
# THIS launcher's own conversion logic changes independently of lstar.
def _launcher_version() -> str:
    try:
        import importlib.metadata as _md
        return "lstar-sc/" + _md.version("lstar-sc")
    except Exception:  # noqa: BLE001
        return "lstar-sc/unknown"

# viewer@0.1 optimization is done by lstar's `convert --viewer` (in `_convert_any`),
# NOT pagoda3's prep.ts (WASM). prep.ts needs node >= 22, unavailable on prod /
# old-glibc hosts (native node fails to build), so it silently skipped there —
# leaving every conversion with the "Not viewer-optimized" banner. `--viewer` is
# node-free and, since lstar-sc >=0.1.7, auto-falls-back raw→lognorm for sources
# with no raw counts. Optimization is thus lstar's job → the cache keys purely on
# the lstar-sc version (no launcher-local suffix needed).
LAUNCHER_VERSION = _launcher_version()   # optimization delegated to lstar convert --viewer
_STORE_SUFFIX = ".lstar.zarr"
_ZIP_SUFFIX = ".lstar.zarr.zip"


def pagoda3_dist_path() -> Path:
    """Where pagoda3's built web bundle lives — the single source of truth for every
    consumer (the `/pagoda3` route + prep below). It is the viewer-pagoda3 MODULE's
    vendored dist, kept ENTIRELY within $ABA_HOME (a deployed ABA never reaches into
    other paths in $HOME). A developer can point ABA at a local build EXPLICITLY via
    $ABA_PAGODA3_DIST — the only outside-$ABA_HOME path, and only when opted in.
    Returns the expected location even if absent, so a caller can report a clean
    'not present' (→ the module installs it) rather than guessing."""
    env = os.getenv("ABA_PAGODA3_DIST")
    if env:
        return Path(env)
    home = Path(os.getenv("ABA_HOME") or (Path.home() / ".aba"))
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


def _convert_any(src: Path, out: Path, set_phase=None) -> None:
    """Convert any lstar-supported source into a `.lstar.zarr` directory store via
    the lstar CLI — ONE entry point for `.h5ad` / `.h5mu` (Python) and, when R +
    the lstar R package are present, Seurat / SingleCellExperiment / pagoda2 /
    conos `.rds` (lstar bridges to Rscript). `--to store` forces store output
    regardless of the temp path's `.building` suffix; `--viewer` optimizes it to the
    `viewer@0.1` profile (od_score, per-group stats/markers, cell-major counts) so
    it opens WITHOUT the "Not viewer-optimized" banner.

    In-process + node-free (no prep.ts / node ≥22 — unavailable on prod/old-glibc).
    lstar-sc >=0.1.7's `--viewer` auto-falls-back raw→lognorm when the source has no
    raw counts (a scaled/log-normalized scanpy `.h5ad`), so it optimizes those too.
    If `--viewer` fails on unusual input, fall back to a plain (functional,
    un-optimized) store rather than failing the launch. `set_phase` reports the
    sub-step to the launch page."""
    import subprocess
    import sys
    sp = set_phase or (lambda *_: None)
    env = {**os.environ}
    rs = _rscript()
    if rs and not env.get("LSTAR_RSCRIPT"):
        env["LSTAR_RSCRIPT"] = rs      # point lstar's .rds bridge at an R with the lstar pkg
    base = [sys.executable, "-m", "lstar", "convert", str(src), str(out), "--to", "store"]
    sp(f"Converting {src.name} → optimized viewer store…")
    r = subprocess.run(base + ["--viewer"], capture_output=True, text=True, timeout=1800, env=env)
    if r.returncode != 0:
        # --viewer failed on odd input — don't fail the launch: retry a plain convert
        # so the viewer still opens (it recomputes DE/HVG per session — the banner).
        shutil.rmtree(out, ignore_errors=True)
        sp(f"Converting {src.name} → viewer store (optimization skipped)…")
        r = subprocess.run(base, capture_output=True, text=True, timeout=1800, env=env)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-600:]
            raise RuntimeError(
                f"lstar convert failed for {src.name!r} (exit {r.returncode}): {tail}")


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


def _serve_native_store(src: Path, cache_dir: Path, out_name: str,
                        project_root: Path, set_phase=None) -> Path:
    """Place an already-built `.lstar.zarr` DIRECTORY store where the store route
    can serve it, WITHOUT copying the tree when avoidable.

    A store inside the project (the usual case — a run wrote it under work/) is
    SYMLINKED into pagoda3/: the store route follows a project-internal symlink,
    so there's no reason to duplicate a possibly-multi-GB tree on every open. A
    store OUTSIDE the project (a registered external path the route can't reach
    through the project sandbox) is copied in as a fallback. Idempotent: an
    existing correct symlink is reused; a stale/wrong one is replaced."""
    sp = set_phase or (lambda *_: None)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / out_name
    real = src.resolve()
    if out.is_symlink() and out.exists() and out.resolve() == real:
        return out                              # already linked to this store
    if out.is_symlink() or out.is_file():
        out.unlink()                            # replace a stale/dangling link
    elif out.exists():
        shutil.rmtree(out, ignore_errors=True)  # replace an old copied tree
    try:
        real.relative_to(project_root.resolve())
        inside = True
    except ValueError:
        inside = False
    if inside:
        sp("Linking store…")
        out.symlink_to(real, target_is_directory=True)
    else:
        sp("Copying store…")
        shutil.copytree(real, out)
    return out


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
    # First-use gating (misc/modules.md): the pagoda3 viewer is a MODULE. If its dist
    # isn't installed, install it HERE — on the prepare job — and WAIT with progress, so
    # a failure surfaces as this job's error (→ the launch page routes it to Guide, the
    # same seam as a conversion failure). The .lstar.zarr conversion below uses the CORE
    # reader, independent of the viewer module.
    if not (pagoda3_dist_path() / "index.html").is_file():
        from core.modules.reconciler import install_and_wait
        ok, err = install_and_wait("viewer-pagoda3", on_progress=lambda m: set_phase(m))
        if not ok:
            raise RuntimeError(err or "The pagoda3 viewer failed to install.")
    src = _resolve_source(node, pid)
    if not src.exists():
        raise FileNotFoundError(
            f"pagoda3: source not found for {node.get('name') or node.get('path')!r}")

    root = project_root(pid)
    cache_dir = root / "pagoda3"
    name = src.name.lower()
    # Strip the (possibly two-part) suffix for a clean output name.
    if name.endswith(_ZIP_SUFFIX):
        suffix = _ZIP_SUFFIX          # native store, zipped → unzip
    elif name.endswith(_STORE_SUFFIX):
        suffix = _STORE_SUFFIX        # native store, directory → serve in place
    else:
        suffix = None                 # .h5ad / .h5mu / .rds → convert (lstar CLI)
    stem = src.name[:-len(suffix)] if suffix else src.stem
    tag = hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]
    out_name = f"{stem}-{tag}{_STORE_SUFFIX}"

    if suffix == _STORE_SUFFIX:
        # Already a store — nothing to derive; symlink it into the served dir
        # (copy only if it lives outside the project). No ensure_derived cache:
        # the store IS the source, so there's nothing to key on or rebuild.
        store = _serve_native_store(src, cache_dir, out_name, root, set_phase)
    else:
        base_convert = _unzip_store if suffix == _ZIP_SUFFIX else _convert_any
        def convert(s: Path, o: Path) -> None:  # bind set_phase into the 2-arg callback
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
