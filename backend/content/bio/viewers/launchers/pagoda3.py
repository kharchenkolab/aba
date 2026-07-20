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
from core import config

# Cache version = the installed lstar-sc version, so upgrading it (e.g. 0.1.x →
# 0.2.0, which switched the on-disk store to zarr v3) AUTOMATICALLY
# re-derives every cached store — no manual bump needed. Suffix `+N` here only if
# THIS launcher's own conversion logic changes independently of lstar.
def _lstar_python(pid: "str | None" = None) -> str:
    """The interpreter that has lstar-sc — the CONVERTER (import name `lstar`).
    W3.4: on a pack deployment lstar-sc ships in the python base pack, so it
    lives in the PROJECT'S weft SESSION, NOT the backend process (`sys.executable`
    is the controller / served base). Resolve the session python when a pack is
    active; fall back to this process otherwise (served-base deploy — lstar-sc
    is pinned into that env). Best-effort: any resolution error → sys.executable
    (the launcher's own error surfaces if lstar truly isn't reachable)."""
    import sys
    try:
        from core.compute import base_env, project_env
        from core import projects
        if base_env.active("python"):
            _pid = str(pid or projects.current() or "_none")
            return str(project_env.interpreter(_pid, "python"))
    except Exception:  # noqa: BLE001
        pass
    return sys.executable


def _launcher_version(python_exe: "str | None" = None) -> str:
    """The lstar-sc version = the convert-cache key, so an lstar upgrade
    auto-rederives stores. Read it from the SAME interpreter that runs the
    convert (the session python on a pack deploy), not the backend process —
    else a pack deployment always keys on 'unknown' and never rederives."""
    import subprocess
    py = python_exe or _lstar_python()
    try:
        r = subprocess.run(
            [py, "-c", "import importlib.metadata as m; print(m.version('lstar-sc'))"],
            capture_output=True, text=True, timeout=30)
        v = (r.stdout or "").strip()
        if r.returncode == 0 and v:
            return "lstar-sc/" + v
    except Exception:  # noqa: BLE001
        pass
    return "lstar-sc/unknown"

# viewer@0.1 optimization is done by lstar's `convert --viewer` (in `_convert_any`),
# NOT pagoda3's prep.ts (WASM). prep.ts needs node >= 22, unavailable on prod /
# old-glibc hosts (native node fails to build), so it silently skipped there —
# leaving every conversion with the "Not viewer-optimized" banner. `--viewer` is
# node-free and, since lstar-sc >=0.1.7, auto-falls-back raw→lognorm for sources
# with no raw counts. Optimization is thus lstar's job → the cache keys purely on
# the lstar-sc version (no launcher-local suffix needed).
# NOTE: the real cache key is computed PER-LAUNCH from the project session's
# lstar-sc (see launch()), because on a pack deployment the version lives in the
# session, not this process. This module-level value is only a legacy fallback.
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
    env = config.settings.pagoda3_dist.get()
    if env:
        return Path(env)
    home = Path(config.settings.home_dir.get() or (Path.home() / ".aba"))
    return home / "vendor" / "pagoda3" / "dist"


def _rscript(pid: "str | None" = None) -> "str | None":
    """The Rscript lstar's R bridge uses for `.rds` conversions. Two sources
    only: the R base pack's project session (the substrate-resolved
    interpreter), or an explicit `$LSTAR_RSCRIPT` operator override. The
    legacy silent fallbacks (tools-env R, system-PATH R) are retired with the
    cutover — a converter quietly running an unmanaged interpreter is the
    silent-lane-switch class; when neither source resolves the caller surfaces
    the honest cause (enable the R pack, or set LSTAR_RSCRIPT)."""
    cands: list = []
    try:
        from core.compute import base_env, project_env
        from core import projects
        if base_env.active("r"):
            _pid = str(pid or projects.current() or "_none")
            cands.append(str(project_env.interpreter(_pid, "r")))
    except Exception:  # noqa: BLE001
        pass
    override = os.getenv("LSTAR_RSCRIPT")
    if override:
        print(f"[pagoda3] using operator-override Rscript ($LSTAR_RSCRIPT)",
              flush=True)
        cands.append(override)
    for cand in cands:
        if cand and os.path.exists(cand):
            return cand
    return None


def _convert_any(src: Path, out: Path, set_phase=None,
                 python_exe: "str | None" = None,
                 rscript: "str | None" = None) -> None:
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
    sp = set_phase or (lambda *_: None)
    env = {**os.environ}
    py = python_exe or _lstar_python()      # W3.4: the SESSION python (has lstar-sc)
    rs = rscript if rscript is not None else _rscript()
    if rs and not env.get("LSTAR_RSCRIPT"):
        env["LSTAR_RSCRIPT"] = rs      # point lstar's .rds bridge at an R with the lstar pkg
    base = [py, "-m", "lstar", "convert", str(src), str(out), "--to", "store"]
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


def _pack_download(store_dir: "str | Path", dest: "str | Path",
                   python_exe: "str | None" = None) -> None:
    """Pack the directory store into lstar's canonical single-file STORED
    `.lstar.zarr.zip` — produced BY lstar (STORED, metadata first, range-readable)
    so a downloaded archive re-opens identically in pagoda3 / lstar. W3.4: prefer
    an in-process lstar (served-base deploy), else subprocess the SESSION python
    (pack deploy — lstar isn't in the web process); fall back to the generic
    STORED pack only if neither has the packer."""
    try:
        from lstar.zarr_io import _pack_stored_zip
        _pack_stored_zip(str(store_dir), str(dest))
        return
    except Exception:  # noqa: BLE001 — lstar not importable in THIS process
        pass
    py = python_exe or _lstar_python()
    import subprocess
    r = subprocess.run(
        [py, "-c",
         "import sys; from lstar.zarr_io import _pack_stored_zip; "
         "_pack_stored_zip(sys.argv[1], sys.argv[2])",
         str(store_dir), str(dest)],
        capture_output=True, text=True, timeout=600)
    if r.returncode == 0 and Path(dest).exists():
        return
    from core.viewers.store_serve import zip_store_stored   # generic STORED fallback
    zip_store_stored(Path(store_dir), Path(dest))


def _serve_native_store(src: Path, cache_dir: Path, out_name: str,
                        project_root: Path, set_phase=None) -> Path:
    """Place an already-built `.lstar.zarr` DIRECTORY store where the store route
    can serve it, WITHOUT copying the tree when avoidable.

    A store inside the project OR inside the weft workspace — the retained tree
    (`runs/<label>/<target>/`) or a live kernel jobdir, P3 serve-in-place: weft is
    the system of record, aba holds only references — is SYMLINKED into pagoda3/:
    the store route follows the link (its allowed real-target roots are the
    project + the weft workspace), so a possibly-multi-GB tree is never duplicated
    on open. Only a store outside BOTH (a registered external path) is copied in
    as a fallback. Idempotent: an existing correct symlink is reused; a
    stale/wrong one is replaced."""
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
    allowed = [project_root.resolve()]
    try:
        from core.compute.adapter import weft_workspace
        allowed.append(weft_workspace().resolve())
    except Exception:  # noqa: BLE001 — no weft configured → project-only
        pass
    inside = any(real == r or r in real.parents for r in allowed)
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


def _run_id_for_node(node: dict) -> "str | None":
    """The Run a viewer node belongs to: an explicit `run_id` (set by the
    launch route when it resolved a fresh Run output), else the node's
    `entity_id` → the exec that produced it → its Run. None when the node isn't
    Run-linked (an unregistered path with neither) — the remote tier can't engage."""
    rid = node.get("run_id")
    if rid:
        return rid
    eid = node.get("entity_id")
    if eid:
        from content.bio.lifecycle.runs import run_id_for_entity
        return run_id_for_entity(eid)
    return None


def _resolve_source(node: dict, pid: str, set_phase=None) -> Path:
    """Resolve the node to an on-disk source. Local candidates first — an absolute
    `artifact_path`, project-relative joins, then a basename scan of the project's
    work dirs (a `.lstar.zarr` **directory** store shows at the LOGICAL output path
    but physically lives under `work/<ana_id>/`). When those miss, route through the
    canonical Run resolver (`resolve_run_store`), which is directory-aware and, for a
    remote-produced output, fetches a size-gated local copy home — so a store on
    another site opens the same way a local one does.

    Raises FileNotFoundError naming the site when the output lives on a non-local
    machine and can't be brought home under the gate (so the user sees "on <site> —
    bring it home", not an opaque "source not found"); returns a nonexistent Path
    for the truly-unknown case so the caller surfaces its clean error."""
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
    # Canonical resolver — handles a directory store AND a remote fetch home.
    # The launch is an EXPLICIT user open, so the fetch runs on the guardrail
    # budget and reports progress to the launch page (the action layer owns
    # consent + progress; the resolver only moves what this action asked for).
    run_id = _run_id_for_node(node)
    if run_id and name:
        from content.bio.lifecycle.runs import resolve_run_store, run_output_site
        hit = resolve_run_store(run_id, name, progress=set_phase)
        if hit:
            return Path(hit)
        site = run_output_site(run_id, name)
        if site and site != "local":
            raise FileNotFoundError(
                f"pagoda3: {name!r} lives on {site} — bring it home to view it "
                f"(Keep it, then open); it isn't on this machine yet.")
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
    src = _resolve_source(node, pid, set_phase)
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

    # W3.4: resolve the interpreters ONCE for this launch — the project session's
    # python (has lstar-sc) + R (r-bio pack), and the cache key from that same
    # lstar-sc (so a pack lstar upgrade rederives). Pack-less deploys resolve to
    # sys.executable / tools-env exactly as before.
    _py = _lstar_python(pid)
    _rs = _rscript(pid)
    _cache_ver = _launcher_version(_py)
    if suffix == _STORE_SUFFIX:
        # Already a store — nothing to derive; symlink it into the served dir
        # (copy only if it lives outside the project). No ensure_derived cache:
        # the store IS the source, so there's nothing to key on or rebuild.
        store = _serve_native_store(src, cache_dir, out_name, root, set_phase)
    else:
        base_convert = _unzip_store if suffix == _ZIP_SUFFIX else _convert_any
        def convert(s: Path, o: Path) -> None:  # bind set_phase + interpreters
            if base_convert is _convert_any:
                _convert_any(s, o, set_phase, python_exe=_py, rscript=_rs)
            else:
                base_convert(s, o, set_phase)
        store = ensure_derived(src, cache_dir, out_name, _cache_ver, convert)

    return LaunchResult(
        url=f"/pagoda3/?store=/pagoda3-store/{pid}/{store.name}/",
        label="Explore in pagoda3",
        # Origin-shared with the pagoda3 window → its copilot proxies through ABA.
        set_local_storage={"p3-agent-proxy": "/pagoda3-api"},
        # The prepared .lstar.zarr on disk — the download endpoint packs THIS
        # (cache-shared with viewing) into lstar's single-file STORED .lstar.zarr.zip.
        store_path=str(store),
        download_packer=lambda sd, d: _pack_download(sd, d, python_exe=_py),
    )


register_launcher("pagoda3_launcher", launch)
