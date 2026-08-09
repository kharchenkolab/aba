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
def _lstar_py_argv(pid: "str | None", py_args: "list[str]") -> "list[str]":
    """Full argv to run the SESSION python with `py_args` (e.g.
    ['-m','lstar','convert',…]). On a mount-adopted base the session interpreter
    AND lstar-sc live only INSIDE the session's activation/mount namespace, so a
    bare exec of the interpreter path runs OUTSIDE it and dies with
    'No module named lstar' — the launcher then fell back to the controller venv
    and failed the same way (live 2026-07-21). Route through the session runtime
    (activation + `unshare -rm` when the base is a squashfs mount) — the SAME
    builder run_python uses (`direct_exec:false` means the prefix path is not
    bare-execable). Pack-less served-base deploy → bare sys.executable (lstar-sc
    is pinned into it). Best-effort: any resolution error → sys.executable."""
    import sys
    try:
        from core.compute import base_env, project_env
        from core import projects
        if base_env.active("python"):
            _pid = str(pid or projects.current() or "_none")
            return project_env.exec_argv(_pid, "python", list(py_args))
    except Exception:  # noqa: BLE001
        pass
    return [sys.executable, *py_args]


def _launcher_version(pid: "str | None" = None) -> str:
    """The lstar-sc version = the convert-cache key, so an lstar upgrade
    auto-rederives stores. Read it from the SAME session env that runs the
    convert (the session python on a pack deploy), not the backend process —
    else a pack deployment always keys on 'unknown' and never rederives."""
    import subprocess
    try:
        r = subprocess.run(
            _lstar_py_argv(pid, ["-c", "import importlib.metadata as m; "
                                       "print(m.version('lstar-sc'))"]),
            capture_output=True, text=True, timeout=60)
        v = (r.stdout or "").strip().splitlines()[-1:] or [""]
        if r.returncode == 0 and v[0]:
            return "lstar-sc/" + v[0]
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


def _rscript_shim(pid: str) -> "str | None":
    """An executable that runs the session's Rscript through its activation.

    `LSTAR_RSCRIPT` is an interpreter PATH — lstar execs it — so on a base whose
    prefix exists only inside its activation's mount namespace there is nothing
    to hand over. A one-line shim closes that: the argv the substrate builds for
    us (`project_env.exec_argv`, which is activation- and namespace-aware) is
    written into a script file, and the file path is a perfectly ordinary
    executable. Returns None if the argv can't be built at all.
    """
    import shlex
    import stat
    try:
        from core.compute import project_env
        from core.config import project_work_dir
        rt = project_env.runtime(pid, "r")
    except Exception:  # noqa: BLE001
        return None
    act = rt.get("activation")
    if not act:
        return None
    # `direct_exec` is NOT sufficient for R. It says the prefix is execable; it
    # does not say the environment is complete. A pack's `cran:` layer lives in
    # a separate `<env>/rlib` and reaches R only via `R_LIBS`, which only the
    # activation exports — so a bare `<prefix>/bin/Rscript` handed to lstar is
    # blind to every cran package the pack declares (measured 2026-08-08:
    # `library(lstar)` failed against an env that had lstar 0.2.2 installed).
    # ONE owner for that rule: project_env._needs_activation, the same predicate
    # argv_for_runtime uses. This used to decide it independently and got it
    # wrong in the same way, which is the argument for sharing it.
    if not project_env._needs_activation("r", rt) and rt.get("prefix"):
        return None                       # execable AND complete — no shim wanted
    # Mirror argv_for_runtime's shapes, but with the forwarded arguments INSIDE
    # the activated shell. `bash -c <script> <argv0> "$@"` is the required idiom:
    # words after the script become $0, $1, … for it, so appending "$@" outside
    # the quoted script would hand the args to bash rather than to Rscript.
    inner = f'{act} && exec Rscript "$@"'
    if rt.get("ns_wrap"):
        # lstar execs this shim from INSIDE the caller's activated env, whose
        # PATH no longer carries the squashfs mount helper — so `act` (which
        # mounts this env) must be able to find it. Same repair as
        # argv_for_runtime; one owner for the rule.
        from core.compute.project_env import _keep_mount_tooling
        inner = f'{_keep_mount_tooling()}{inner}'
    body = f'bash -c {shlex.quote(inner)} aba-rscript-shim "$@"'
    if rt.get("ns_wrap"):
        body = f'unshare -rm {body}'
    try:
        d = Path(project_work_dir(pid)) / ".aba"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "rscript-shim.sh"
        p.write_text(f"#!/usr/bin/env bash\nexec {body}\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        return str(p)
    except OSError:
        return None


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
            # The shim comes FIRST when R needs its activation, not only when
            # `interpreter()` refuses. On a directly-execable prefix it does
            # NOT refuse — it hands back `<prefix>/bin/Rscript`, which cannot
            # see the pack's `cran:` layer (a separate `rlib` reached only via
            # the activation's R_LIBS). That path looked healthy and silently
            # dropped every cran package, lstar included.
            try:
                if project_env._needs_activation(
                        "r", project_env.runtime(_pid, "r")):
                    _shim = _rscript_shim(_pid)
                    if _shim:
                        cands.append(_shim)
            except Exception:  # noqa: BLE001 — fall through to the plain path
                pass
            try:
                if not cands:
                    cands.append(str(project_env.interpreter(_pid, "r")))
            except Exception as e:  # noqa: BLE001
                # A MOUNT-SCOPED R base has no interpreter path usable outside
                # its activation, so `interpreter()` refuses (session.
                # no_direct_exec) — the same topology that broke the Python side.
                # Swallowing that silently dropped the .rds bridge with no
                # signal at all. Wrap the session instead: a tiny exec shim that
                # activates and hands off to Rscript IS a real executable path,
                # which is what lstar's LSTAR_RSCRIPT contract needs.
                shim = _rscript_shim(_pid)
                print(f"[pagoda3] R interpreter is not directly execable "
                      f"({type(e).__name__}: {str(e)[:120]}) — "
                      f"{'using an activation shim' if shim else 'NO shim could be built'}",
                      flush=True)
                if shim:
                    cands.append(shim)
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
                 pid: "str | None" = None,
                 rscript: "str | None" = None) -> None:
    """Convert any lstar-supported source into a `.lstar.zarr` directory store via
    the lstar CLI — ONE entry point for `.h5ad` / `.h5mu` (Python) and, when R +
    the lstar R package are present, Seurat / SingleCellExperiment / pagoda2 /
    conos `.rds` (lstar bridges to Rscript). `--to store` forces store output
    regardless of the temp path's `.building` suffix; `--viewer` optimizes it to the
    `viewer@0.1` profile (od_score, per-group stats/markers, cell-major counts) so
    it opens WITHOUT the "Not viewer-optimized" banner.

    Runs `lstar` INSIDE the project's session env via the runtime activation (see
    `_lstar_py_argv`) — on a mount-adopted base a bare exec of the session python
    can't import lstar. The full command is built PER attempt (the argv may be an
    `unshare -rm bash -c '… && exec python …'` wrapper, so flags can't be appended
    after the fact). In-process + node-free (no prep.ts / node ≥22 — unavailable on
    prod/old-glibc). lstar-sc >=0.1.7's `--viewer` auto-falls-back raw→lognorm when
    the source has no raw counts, so it optimizes those too. If `--viewer` fails on
    unusual input, fall back to a plain (functional, un-optimized) store rather than
    failing the launch. `set_phase` reports the sub-step to the launch page."""
    import subprocess
    sp = set_phase or (lambda *_: None)
    env = {**os.environ}
    rs = rscript if rscript is not None else _rscript(pid)
    if rs and not env.get("LSTAR_RSCRIPT"):
        env["LSTAR_RSCRIPT"] = rs      # point lstar's .rds bridge at an R with the lstar pkg

    def _run(extra: "list[str]"):
        argv = _lstar_py_argv(pid, ["-m", "lstar", "convert", str(src), str(out),
                                    "--to", "store", *extra])
        return subprocess.run(argv, capture_output=True, text=True, timeout=1800, env=env)

    sp(f"Converting {src.name} → optimized viewer store…")
    r = _run(["--viewer"])
    if r.returncode != 0:
        # --viewer failed on odd input — don't fail the launch: retry a plain convert
        # so the viewer still opens (it recomputes DE/HVG per session — the banner).
        shutil.rmtree(out, ignore_errors=True)
        sp(f"Converting {src.name} → viewer store (optimization skipped)…")
        r = _run([])
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-600:]
            raise RuntimeError(
                f"lstar convert failed for {src.name!r} (exit {r.returncode}): {tail}")
    # A convert with a CURRENT lstar always leaves a gene-major basis. If it did
    # not, the store is not the problem — the SESSION ENV is behind, and the
    # viewer would fail in the browser with nothing pointing here. This is the
    # deployment-migration gap made legible: `install/core/envs/*.yaml` ships on
    # fresh installs only, and `aba update` never overwrites
    # $ABA_HOME/installation/envs, so a long-lived deployment keeps whatever
    # lstar it was installed with.
    if _viewer_contract(out, pid).get("gene_major") is False:
        raise RuntimeError(
            f"lstar converted {src.name!r} but left the counts cell-major only, "
            f"which the pagoda3 viewer refuses. The session env's {_launcher_version(pid)} "
            f"is older than the 0.2.2 this deployment expects: update the pack in "
            f"$ABA_HOME/installation/envs/python_bio.yaml and re-realize it.")


# ── the viewer@0.1 counts-basis contract ─────────────────────────────────────
#
# A viewer store carries the count payload in BOTH orientations: gene-major
# (csc) so one gene is one column is one byte range, and cell-major (csr) for
# cluster/lasso reads. Before lstar 0.2.2 the prep normalized the basis in
# MEMORY and never wrote it back, so a store built from a CSR source came out
# with two cell-major copies and nothing a gene column could be read from — and
# served a cell's profile as a gene's column, silently.
#
# 0.2.2 writes it back, stamps `provenance.viewer="basis"`, and makes the
# absence an ERROR; the pinned pagoda3 dist checks the same stamp client-side.
# So every store built by an older lstar goes from silently wrong to REFUSED,
# and the refusal lands in the browser where ABA cannot explain it.
#
# Bumping the lstar pin repairs only the CONVERT lane (ensure_derived keys on
# the lstar-sc version, so a bump re-converts). The other two lanes need this:
# the zip lane re-derives but would unpack the same stale bytes, and a native
# store is symlinked, never rebuilt at all.

# Metadata-only probe, run in the SESSION env (where lstar lives).
#
# Deliberately NOT `lstar.validate(ds)`: validate touches field VALUES
# (`sp.issparse(f.values)`, `np.asarray(f.values)`), so on a lazily-opened store
# it both defeats the laziness and mis-reports — a LazyCSX is not a scipy sparse
# matrix, so the csc/csr branch would fall through to a spurious shape error. A
# store can be tens of GB; opening it eagerly on every launch is not an option.
#
# The basis-selection rule below MIRRORS lstar's (`validate._viewer_basis` at
# 0.2.2: stamped field, else one named `counts`, else the sole 2-D measure
# spanning what `counts_cellmajor` spans). Mirroring a rule from another repo is
# a liability — see misc/from-aba-viewer-contract.md, which asks lstar for a
# public metadata-only contract check so this can be deleted.
_CONTRACT_PROBE = r"""
import json, sys
import lstar
ds = lstar.read(sys.argv[1], lazy=True)          # lazy: never materialize to ask a metadata question
basis = None
for _n, _f in ds.fields.items():
    if (_f.provenance or {}).get("viewer") == "basis":
        basis = (_n, _f); break
if basis is None and ds.fields.get("counts") is not None:
    basis = ("counts", ds.fields["counts"])
if basis is None:
    _cm = ds.fields.get("counts_cellmajor")
    _span = list(_cm.span or []) if _cm is not None else []
    _c = [(n, f) for n, f in ds.fields.items()
          if n != "counts_cellmajor" and f.role == "measure" and list(f.span or []) == _span]
    basis = _c[0] if len(_c) == 1 else None
print("ABA_CONTRACT " + json.dumps({
    "basis": basis[0] if basis else None,
    "encoding": (basis[1].encoding if basis else None),
    "gene_major": bool(basis is not None and basis[1].encoding == "csc"),
}))
"""


def _viewer_contract(store: Path, pid: "str | None" = None) -> dict:
    """Is `store`'s count basis gene-major? Metadata only; never mutates.

    `gene_major` is TRI-STATE and the None matters: True = contract met, False =
    the store needs a repair, **None = could not tell** (no lstar, an unreadable
    store, a probe that raised). A caller must not treat None as either — it is
    the one case where doing nothing is right, because the alternatives are
    rewriting a store on a guess and refusing to open one that may be fine."""
    import json
    import subprocess
    try:
        r = subprocess.run(_lstar_py_argv(pid, ["-c", _CONTRACT_PROBE, str(store)]),
                           capture_output=True, text=True, timeout=300)
    except Exception as e:  # noqa: BLE001 — the probe must never fail a launch
        return {"gene_major": None, "why": str(e)}
    line = next((l for l in (r.stdout or "").splitlines()
                 if l.startswith("ABA_CONTRACT ")), None)
    if r.returncode != 0 or not line:
        return {"gene_major": None,
                "why": (r.stderr or r.stdout or "").strip()[-400:] or "no probe output"}
    try:
        return json.loads(line[len("ABA_CONTRACT "):])
    except Exception as e:  # noqa: BLE001
        return {"gene_major": None, "why": str(e)}


def _repair_viewer_store(store: Path, pid: "str | None" = None,
                         set_phase=None) -> bool:
    """Re-run lstar's viewer prep over `store` IN PLACE — lstar's own documented
    repair for a store written before 0.2.2 ("re-run extend_for_viewer"). It
    rewrites the basis gene-major and prunes chunks the new layout no longer
    covers, so the store's BYTES change; any cached copy of them must be
    invalidated (the range cache keys on a freshness digest and wipes when it
    cannot prove the store is unchanged, which covers this).

    ONLY ever called on bytes ABA owns — its own cache dir. The native lane
    copies first rather than repairing a store in the project tree or the weft
    workspace: weft is the system of record there, and a viewer opening a file
    is not a licence to rewrite it."""
    import subprocess
    (set_phase or (lambda *_: None))("Repairing viewer store…")
    r = subprocess.run(_lstar_py_argv(pid, ["-m", "lstar", "viewer", str(store)]),
                       capture_output=True, text=True, timeout=1800)
    return r.returncode == 0


# ── the same contract, for a store that is STREAMED and never comes home ─────
#
# The three local lanes above probe with lstar and repair. Neither is possible
# for a remote store: its bytes are a run's retained output on another site, and
# the whole point of the range channel is that they never come home. So this
# lane DETECTS and REPORTS — it never writes.
#
# Detection needs no lstar on the far side and no materialization, because the
# answer lives in metadata. A `.lstar.zarr` store is a directory of small JSON
# metadata files beside the big arrays: the root carries `profiles` (is this a
# viewer store at all) and the field list; a field's own metadata carries its
# `encoding`. Two small reads over the data plane, against a store of any size.
#
# Measured 2026-08-08 against a real remote store: the root read was 103 KB and
# the field read a few hundred bytes.
_ROOT_META = "zarr.json"
_BASIS_CANDIDATE = "counts"          # lstar's own second selection rule
_META_MAX = 4 * 1024 * 1024


def _remote_meta(entry: dict, rel: str) -> "dict | None":
    """Read one small JSON metadata member of a remote store, by whichever arm
    the registry row carries. None on any failure — this is a diagnostic, and a
    diagnostic that raises into a launch is worse than one that abstains."""
    import base64
    import json
    from core.compute import retention
    try:
        if entry.get("ref") is not None:
            r = retention.data_read_range(entry["ref"], rel=rel, offset=0,
                                          length=_META_MAX, site=entry.get("site"))
        else:
            base = (entry.get("base_rel") or "").rstrip("/")
            r = retention.file_read(entry["target"],
                                    f"{base}/{rel}" if base else rel,
                                    max_bytes=_META_MAX)
        b64 = r.get("bytes_b64")
        if not b64:
            return None
        return json.loads(base64.b64decode(b64).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — absent verb, missing member, bad JSON
        return None


def _lstar_attrs(doc: "dict | None") -> dict:
    """The `lstar` block of a zarr metadata document, v3 (`attributes.lstar`) or
    v2 (`lstar` at the root). Empty dict when it is neither."""
    if not isinstance(doc, dict):
        return {}
    a = doc.get("attributes")
    ls = (a or {}).get("lstar") if isinstance(a, dict) else None
    if not isinstance(ls, dict):
        ls = doc.get("lstar")
    return ls if isinstance(ls, dict) else {}


def remote_viewer_contract(entry: dict) -> dict:
    """Does a STREAMED store satisfy the viewer's gene-major basis contract?

    Returns `{gene_major, basis, encoding, why}` with the SAME tri-state as the
    local probe: True = fine, False = the viewer will refuse it, **None = could
    not tell**. None is the common answer here and must stay harmless — an older
    substrate without the read verb, a store that is not viewer-profiled, a
    basis that is not the conventionally-named field.

    Deliberately narrower than lstar's full selection rule: it answers only for
    the shape it can answer for (a `viewer@0.1` store whose basis is the field
    named `counts`), and abstains otherwise. A remote guess is worse than a
    remote shrug — the local lanes can repair a wrong answer, this one can only
    tell the user something false."""
    root = _lstar_attrs(_remote_meta(entry, _ROOT_META))
    if not root:
        return {"gene_major": None, "why": "store metadata unreadable"}
    profiles = root.get("profiles") or []
    if not any(str(p).startswith("viewer@") for p in profiles):
        return {"gene_major": None, "why": "not a viewer-profiled store"}
    fields = root.get("fields") or []
    if _BASIS_CANDIDATE not in fields:
        # A stamped or differently-named basis: lstar's rule would find it, this
        # probe will not, and inventing an answer is the one thing it must not do.
        return {"gene_major": None,
                "why": f"no field named {_BASIS_CANDIDATE!r} — basis not identifiable "
                       f"from metadata alone"}
    fmeta = _lstar_attrs(_remote_meta(entry, f"fields/{_BASIS_CANDIDATE}/{_ROOT_META}"))
    enc = fmeta.get("encoding")
    if enc is None:
        return {"gene_major": None, "why": "basis metadata unreadable"}
    return {"gene_major": enc == "csc", "basis": _BASIS_CANDIDATE, "encoding": enc}


def remote_contract_warning(entry: dict, store_rel: "str | None" = None) -> "str | None":
    """The user-facing sentence for a streamed store that will be refused, or
    None when there is nothing to say (fine, or unknown).

    It names the FIX and the machine to run it on, because that is the only
    action available: ABA cannot repair a store it is deliberately not holding.
    Without this the failure lands in the browser as a viewer contract error
    with nothing anywhere explaining what to do."""
    got = remote_viewer_contract(entry)
    if got.get("gene_major") is not False:
        return None
    site = entry.get("site") or "the remote site"
    where = store_rel or "<store>"
    return (f"This store's counts are cell-major only, so the pagoda3 viewer "
            f"will refuse to colour by gene. Its bytes live on {site} and are "
            f"streamed, so ABA cannot repair them from here — run "
            f"`lstar viewer {where}` on {site} (lstar >=0.2.2), then reopen.")


def _pack_download(store_dir: "str | Path", dest: "str | Path",
                   pid: "str | None" = None) -> None:
    """Pack the directory store into lstar's canonical single-file STORED
    `.lstar.zarr.zip` — produced BY lstar (STORED, metadata first, range-readable)
    so a downloaded archive re-opens identically in pagoda3 / lstar. W3.4: prefer
    an in-process lstar (served-base deploy), else run lstar in the SESSION env
    (pack deploy — lstar isn't in the web process, and on a mount base its python
    is only usable through the runtime activation); fall back to the generic
    STORED pack only if neither has the packer."""
    try:
        from lstar.zarr_io import _pack_stored_zip
        _pack_stored_zip(str(store_dir), str(dest))
        return
    except Exception:  # noqa: BLE001 — lstar not importable in THIS process
        pass
    import subprocess
    r = subprocess.run(
        _lstar_py_argv(pid, [
            "-c", "import sys; from lstar.zarr_io import _pack_stored_zip; "
                  "_pack_stored_zip(sys.argv[1], sys.argv[2])",
            str(store_dir), str(dest)]),
        capture_output=True, text=True, timeout=600)
    if r.returncode == 0 and Path(dest).exists():
        return
    from core.viewers.store_serve import zip_store_stored   # generic STORED fallback
    zip_store_stored(Path(store_dir), Path(dest))


def _serve_native_store(src: Path, cache_dir: Path, out_name: str,
                        project_root: Path, set_phase=None,
                        pid: "str | None" = None) -> Path:
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
    # A store written before lstar 0.2.2 has a cell-major-only basis, which the
    # pinned pagoda3 dist refuses. It cannot be symlinked and served as-is, and
    # it must not be repaired in place: `real` is a run's retained output or a
    # live jobdir — weft's system of record, not ours. So take a COPY and repair
    # that. The copy is the thing symlinking exists to avoid, which is why it is
    # conditional: only a legacy store pays it, once, and only when the probe is
    # SURE (None = couldn't tell → link as before rather than duplicate a tree
    # on a guess).
    if inside and _viewer_contract(real, pid).get("gene_major") is False:
        sp("Upgrading store for the viewer (one-off copy)…")
        shutil.copytree(real, out)
        _repair_viewer_store(out, pid, sp)
        return out
    if inside:
        sp("Linking store…")
        out.symlink_to(real, target_is_directory=True)
    else:
        sp("Copying store…")
        shutil.copytree(real, out)
        if _viewer_contract(out, pid).get("gene_major") is False:
            _repair_viewer_store(out, pid, sp)
    return out


def _unzip_store(src: Path, out: Path, set_phase=None,
                 pid: "str | None" = None) -> None:
    """Native store shipped as a .lstar.zarr.zip — extract into a directory the
    store route can serve (the browser can't range-read a zip over HTTP). The
    archive's root IS the store root (.zattrs/axes/fields at top level).

    Then repair the extraction if its basis is cell-major. This lane looked
    covered and was not: it runs under `ensure_derived`, which keys on the
    lstar-sc version, so bumping lstar DOES re-derive — and re-derives by
    unpacking the same stale bytes. Version-keyed rebuild only heals a lane that
    RE-COMPUTES; unzip copies. The repair is safe here because the extraction is
    ABA's own copy: the archive is not touched."""
    import zipfile
    sp = set_phase or (lambda *_: None)
    sp("Unpacking store…")
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        z.extractall(out)
    if _viewer_contract(out, pid).get("gene_major") is False:
        _repair_viewer_store(out, pid, sp)
        if _viewer_contract(out, pid).get("gene_major") is False:
            raise RuntimeError(
                f"{src.name}: this store's counts are cell-major only, and the "
                f"viewer repair did not fix it. pagoda3 needs a gene-major "
                f"basis to colour by gene; re-export the store with lstar "
                f">=0.2.2 (`lstar viewer <store>`).")


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


def ref_stream_facts(e: "dict | None", name: str) -> "dict | None":
    """THE recorded-facts eligibility decision for the REF arm — ONE predicate
    consumed by BOTH the launcher registration (`_register_ref_arm`) and the
    pre-flight note (`_remote_stream_ready`, content/bio/tools/viewers.py), so
    the note can never promise a stream the launcher would decline (a weaker
    note-side gate once said "chunks stream on demand" for a remote by-ref FILE
    that launch then materialized straight into the transfer gate — the exact
    over-promising class the shared-note refactor exists to kill; the agreement
    matrix in tests/test_range_channel.py guards the parity). RECORDED FACTS
    ONLY — never probes a verb, a disk, or the substrate; callers own the
    per-verb probe.

    Eligible iff: `name` is the directory-store viewer shape (`.lstar.zarr` —
    a FILE would need conversion, which needs local bytes); the entity records
    a data-plane content `ref` OR a MINTABLE identity (ref absent but a durable
    home path — `home.path` / `ref_path` — recorded: the path-lane registration
    shape, whose ref mints lazily; the launcher mints it at click,
    `_mint_dataset_ref`, and the answer carries `mintable: True` + `path`);
    `dataset_location` says remote + by-reference; AND the recorded payload
    shape CONFIRMS a directory tree — descriptor/fingerprint `n_files >= 2`
    (a single FILE fingerprints as n_files=1, `core/data/external_ref.py`;
    a chunked store always has metadata + chunk members). An absent or
    inconclusive shape REFUSES to the materialize path: a FILE-shaped ref
    wearing the store suffix would stream-register and then mute-404 every
    chunk (the substrate refuses rel-on-FILE), where the whole-fetch path
    handles both shapes correctly — refusal costs one gated fetch, admission
    costs a dead viewer.

    The mintable answer is a PROMISE the note may repeat: the one accepted
    divergence is a mint FAILURE at click, which degrades that launch to the
    materialize / honesty-bridge path after the note said streaming — the
    mirror lever is unaffected, so no hedging wording is required.

    Returns the registration facts `{ref, site, size, digest}` (plus
    `mintable, path` when the ref is to be minted), or None. Never raises."""
    try:
        if not name.endswith(_STORE_SUFFIX):
            return None                         # only a dir store can stream a ref
        md = (e or {}).get("metadata") or {}
        ref = md.get("ref")
        mint_path = (md.get("home") or {}).get("path") or md.get("ref_path")
        if not ref and not mint_path:
            return None             # no identity and nothing to mint one from
        from content.bio.data_location import dataset_location
        loc = dataset_location(e or {})
        if not (loc.get("remote") and loc.get("by_reference")):
            return None                         # local / adopted → materialize path
        if loc.get("mirrored"):
            # The bytes are already HERE. launch() registers the stream before
            # it resolves a local source, so without this the mirror lever
            # ("Mirror here & retry") could never take effect and a mirrored
            # store would back-haul every chunk over the WAN with the whole
            # tree on local disk. Confirmed against the disk (a LOCAL stat on
            # the controller — not the inventory round-trip this predicate
            # forbids), because `local_mirror` is never cleared: a mirror the
            # user deleted must fall back to streaming rather than refusing
            # both ways, and the pre-flight note that shares this predicate
            # then stays true in both directions.
            mpath = (md.get("local_mirror") or {}).get("path")
            if mpath and Path(mpath).exists():
                return None
        if md.get("source_changed") or md.get("source_missing"):
            # Recorded as drifted/gone at its source. The ref is minted once and
            # never re-minted, so streaming would keep serving cacheable chunks
            # under a URL whose bytes have changed. Materialize instead (which
            # re-validates) until a Re-check clears the flag.
            return None
        n_files = ((md.get("descriptor") or {}).get("n_files")
                   or (md.get("fingerprint") or {}).get("n_files"))
        if not isinstance(n_files, int) or n_files < 2:
            return None                         # FILE-shaped / unconfirmed → materialize
        out = {"ref": ref, "site": loc.get("site"),
               "size": loc.get("total_bytes"),
               "digest": (md.get("fingerprint") or {}).get("digest")}
        if not ref:
            out["mintable"] = True
            out["path"] = mint_path
        return out
    except Exception:  # noqa: BLE001 — an eligibility read must never raise
        return None


def _mint_dataset_ref(eid: str, facts: dict) -> "str | None":
    """Lazy identity mint at LAUNCH for a mintable by-reference dataset
    (`ref_stream_facts` said `mintable`): register the recorded durable home on
    the data plane exactly as the eager registration lane does —
    `data_register(path, site=, ingest=False)` (core/data/datasets.py) — and
    persist the minted ref onto the entity race-safely (`patch_metadata`, the
    single key "ref"; never a metadata-blob rewrite). Runs inside the async
    prepare/launch job: minting fingerprints the tree ON-SITE (one read pass —
    seconds for a hundreds-of-members store, longer for TB-scale), acceptable
    HERE and NEVER in the link-mint note path. ONE attempt per launch; ANY
    failure (mint or persist) → None with NO metadata write, so the launch
    degrades to exactly today's materialize / honesty-bridge path. Never
    raises."""
    try:
        from core.compute.adapter import get_compute
        r = get_compute().sync_call("data_register", facts["path"],
                                    site=facts["site"], ingest=False)
        ref = r.get("ref") if isinstance(r, dict) else None
        if not ref:
            return None
        from core.graph.entities import patch_metadata
        patch_metadata(eid, {"ref": ref})
        return ref
    except Exception:  # noqa: BLE001 — degrade; a later launch retries the mint
        return None


def _register_ref_arm(node: dict, pid: str) -> "str | None":
    """REF arm (misc/range_channel_plan.md): an entity-backed by-reference
    REMOTE directory store whose recorded metadata carries a data-plane content
    `ref` — or a mintable durable home, whose ref this mints NOW
    (`_mint_dataset_ref`) — streams its chunks addressed by that ref, with NO
    resolvable run required. Eligibility is RECORDED FACTS ONLY plus the
    per-verb probe — no inventory round-trip, unlike the run arm's
    `resolve_remote_store_stream`; only the mintable shape pays a data-plane
    call, here in the async launch job.

    ALL eligibility gates live in `ref_stream_facts` (the one predicate the
    pre-flight note shares — no gate may be added here instead); this function
    adds only what the note doesn't need: the entity fetch, the per-verb probe,
    the mint, and the registration itself. Returns the `store_key` (stable per
    ref) or None. Never raises to the caller (the outer try owns that)."""
    eid = node.get("entity_id")
    if not eid:
        return None
    raw = node.get("artifact_path") or node.get("path") or node.get("name") or ""
    name = Path(raw).name
    from core.compute import retention
    if not retention.range_read_available(retention.DATA_RANGE_VERB):
        return None                             # ref arm absent → run arm / materialize
    from core.graph.entities import get_entity
    facts = ref_stream_facts(get_entity(eid), name)
    if not facts:
        return None
    ref = facts.get("ref") or (_mint_dataset_ref(eid, facts)
                               if facts.get("mintable") else None)
    if not ref:
        return None                 # mint failed → exactly today's path
    from core.viewers.range_cache import register_remote_store
    stem = name[:-len(_STORE_SUFFIX)]
    # store_key stable per ref: the content ref IS the store's identity, so the
    # key changes iff the bytes change (a new ref) — the digest-wipe never needs
    # to fire for the ref arm. `digest` is recorded when the metadata carries a
    # fingerprint (belt-and-suspenders; usually absent for a ref-only shape).
    tag = hashlib.sha1(str(ref).encode()).hexdigest()[:8]
    store_key = f"{stem}-{tag}{_STORE_SUFFIX}"
    register_remote_store(pid, store_key, site=facts["site"], ref=ref,
                          size=facts["size"], digest=facts["digest"])
    return store_key


def _has_local_store_bytes(node: dict) -> bool:
    """True when this node's store is ALREADY readable on this host.

    `launch()` registers a stream before it resolves a local source, so
    without this check a store whose bytes are on local disk still back-hauls
    every chunk over the network — and the launch page's "Mirror here & retry"
    lever can never take effect, because mirroring records `local_mirror` but
    leaves the home remote. Validated against the disk, not just the flag: a
    mirror the user deleted must fall through to streaming rather than
    dead-ending on a path that is no longer there."""
    try:
        ap = node.get("artifact_path") or node.get("path")
        if ap and Path(ap).is_absolute() and Path(ap).exists():
            return True
        eid = node.get("entity_id")
        if not eid:
            return False
        from core.graph.entities import get_entity
        from content.bio.data_location import dataset_location
        e = get_entity(eid) or {}
        if not dataset_location(e).get("mirrored"):
            return False
        lm = ((e.get("metadata") or {}).get("local_mirror") or {}).get("path")
        return bool(lm and Path(lm).exists())
    except Exception:  # noqa: BLE001 — a locality read must never fail a launch
        return False


def _refresh_stream_registration(pid: str, run_id: str, name: str,
                                 expect_key: str) -> None:
    """Background half of the fast-reuse launch: re-resolve the store's remote
    home and re-register, so a re-derived store's changed digest still wipes
    the stale chunk cache — just seconds AFTER the launch instead of gating
    it. Best-effort; a failure leaves the reused row exactly as a failed
    resolve would have today."""
    try:
        from content.bio.lifecycle.runs import resolve_remote_store_stream
        from core.viewers.range_cache import register_remote_store
        home = resolve_remote_store_stream(run_id, name)
        if not home:
            return
        stem = (name[:-len(_STORE_SUFFIX)] if name.endswith(_STORE_SUFFIX)
                else Path(name).stem)
        tag = hashlib.sha1(
            f"{home['site']}|{home['store_rel']}".encode()).hexdigest()[:8]
        if f"{stem}-{tag}{_STORE_SUFFIX}" != expect_key:
            return          # the home MOVED — the next launch resolves fresh
        register_remote_store(pid, expect_key, target=home["target"],
                              base_rel=home["store_rel"], site=home["site"],
                              size=home.get("size"), digest=home.get("digest"))
    except Exception:  # noqa: BLE001
        pass


def _register_remote_stream(node: dict, pid: str) -> "str | None":
    """If this node resolves to a directory store that lives on a REMOTE site AND
    the substrate exposes a ranged-read verb, register the store's remote home
    in the range registry and return the `store_key` to mint the stream URL from
    — else None (the caller falls back to today's whole-store materialize path).
    The store then streams chunk-by-chunk through the store route; NOTHING is
    fetched here and the 2 GiB whole-fetch guardrail is never engaged.

    Two arms, tried cheapest-first: the REF arm (`_register_ref_arm`) serves a
    by-reference remote dataset addressed by its recorded data-plane ref with no
    run and no inventory round-trip; on a miss the RUN arm resolves the
    producing run's remote store home (`resolve_remote_store_stream`, one
    inventory-backed resolve). Best-effort: any failure returns None → today's
    behavior (graceful degradation)."""
    try:
        # Bytes already here → stream on NEITHER arm. The ref arm re-states this
        # inside `ref_stream_facts` (the predicate the pre-flight note shares, so
        # the note stops promising a stream); this covers the RUN arm too, whose
        # locality comes from the producing run's outputs rather than the
        # dataset's own recorded home.
        if _has_local_store_bytes(node):
            return None
        key = _register_ref_arm(node, pid)
        if key:
            return key
        from core.compute import retention
        if not retention.range_read_available():
            return None                         # older substrate → materialize path
        run_id = _run_id_for_node(node)
        raw = node.get("artifact_path") or node.get("path") or node.get("name") or ""
        name = Path(raw).name
        if not run_id or not name:
            return None
        # FAST REUSE: a store this project has streamed before has a registry
        # row carrying the full arm — and the address index can mint the same
        # store_key without any resolve. The run-arm resolve costs several
        # site round-trips (locate + inventory + digest) and was paid on
        # EVERY click: ~6 s of the launch page's "Starting…" (measured live,
        # 2026-08-09). Reuse the row immediately and refresh the digest in
        # the BACKGROUND — the wipe-on-change then lands seconds after the
        # launch instead of gating it, which is no worse a staleness window
        # than today's (the chunk cache already persists BETWEEN launches and
        # only ever wiped at the next launch's resolve). First launch of a
        # store still pays the full resolve.
        try:
            from core.graph import output_addr as _oa
            from core.viewers.range_cache import lookup_remote_store
            row = next((r for r in _oa.by_name(name)
                        if r.get("run_id") == run_id and r.get("site")
                        and r.get("site") != "local" and r.get("rel")), None)
            if row is not None:
                stem0 = (name[:-len(_STORE_SUFFIX)]
                         if name.endswith(_STORE_SUFFIX) else Path(name).stem)
                tag0 = hashlib.sha1(
                    f"{row['site']}|{row['rel']}".encode()).hexdigest()[:8]
                key0 = f"{stem0}-{tag0}{_STORE_SUFFIX}"
                if lookup_remote_store(pid, key0):
                    from core import projects as _prj
                    _prj.spawn(_refresh_stream_registration,
                               pid, run_id, name, key0)
                    return key0
        except Exception:  # noqa: BLE001 — reuse is an optimization only
            pass
        from content.bio.lifecycle.runs import resolve_remote_store_stream
        home = resolve_remote_store_stream(run_id, name)
        if not home:
            return None                         # local / not a dir store / unconfirmed
        from core.viewers.range_cache import register_remote_store
        stem = (name[:-len(_STORE_SUFFIX)] if name.endswith(_STORE_SUFFIX)
                else Path(name).stem)
        tag = hashlib.sha1(f"{home['site']}|{home['store_rel']}".encode()).hexdigest()[:8]
        store_key = f"{stem}-{tag}{_STORE_SUFFIX}"
        register_remote_store(pid, store_key, target=home["target"],
                              base_rel=home["store_rel"], site=home["site"],
                              size=home.get("size"), digest=home.get("digest"))
        return store_key
    except Exception:  # noqa: BLE001 — degradation must never fail the launch
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
    # which the logical output-tree path doesn't map to. Resolve by NAME through
    # the project door, and take a hit only when it is UNAMBIGUOUS — the private
    # glob this replaces silently took newest-wins across same-named files from
    # different runs, the exact anti-pattern the door exists to end. Ambiguity
    # falls through to the run-scoped resolver below: provenance beats mtime.
    name = Path(raw).name
    if name:
        try:
            from content.bio.project_locate import locate_project_files
            loc = [h for h in locate_project_files(name, limit=6).get("matches", [])
                   if h.get("path")]
            if len(loc) == 1:
                return Path(loc[0]["path"])
        except Exception:  # noqa: BLE001 — fallback resolution is best-effort
            pass
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


def _source_not_found(node: dict) -> FileNotFoundError:
    """The terminal error for a source that missed EVERY resolver tier. Honesty
    bridge: when the node is ENTITY-backed and that entity's RECORDED facts say
    the bytes are a by-reference REMOTE home (`dataset_location` — entity-level
    facts only, no probe, no new resolution tier), the generic "source not
    found" wording is a lie of omission — it fails the launch page's remote
    regex, hiding the mirror lever that the entity facts prove WOULD work, and
    contradicts the link-mint pre-flight (which reads the same recorded facts).
    Found live: a by-reference remote dataset whose producing run was
    unresolvable (run entity deleted, target-less exec, keeps forgotten) missed
    the run-keyed remote raise in `_resolve_source` and fell through here. Same
    error SHAPE as that raise ("lives on <site>") so the lever engages.
    Run-keyed streaming deliberately can't serve this class (that needs the
    substrate's registered-data addressing arm — separate work). Non-entity /
    non-by-reference / local sources keep the EXACT generic wording
    (ceiling-guarded)."""
    name = node.get("name") or node.get("path")
    eid = node.get("entity_id")
    if eid:
        try:
            from core.graph.entities import get_entity
            from content.bio.data_location import dataset_location
            loc = dataset_location(get_entity(eid) or {})
            if loc.get("remote") and loc.get("by_reference"):
                return FileNotFoundError(
                    f"pagoda3: {name!r} lives on {loc['site']} — it isn't on "
                    f"this machine, and no run placement can fetch it from "
                    f"here. Mirror the dataset locally (its card has Mirror "
                    f"locally), then reopen.")
        except Exception:  # noqa: BLE001 — the bridge must never mask the plain error
            pass
    return FileNotFoundError(f"pagoda3: source not found for {name!r}")


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

    # Range channel (misc/range_channel_plan.md Phase 1): a directory store whose
    # bytes live on another site streams its chunks on demand through the store
    # route — no whole-store materialize, no 2 GiB guardrail — WHEN the substrate
    # exposes the ranged-read verb. Registration is cheap and moves nothing; on
    # any miss (local output / verb absent / unconfirmed) we fall through to the
    # materialize path below, which keeps its guardrail + mirror lever unchanged.
    streamed_key = _register_remote_stream(node, pid)
    if streamed_key:
        # NOTE: the counts-basis contract is NOT checked here. The ref arm
        # registers from RECORDED FACTS ONLY — no round trip — and a launch is
        # the one place that property is load-bearing; two metadata reads on a
        # 12 Mbit link would put ~1.7 s in front of every streamed open to catch
        # a rare defect. The check rides the PRE-FLIGHT NOTE instead
        # (`_remote_stream_note`), which already probes, runs before the click,
        # and exists precisely to say what opening will do.
        return LaunchResult(
            url=f"/pagoda3/?store=/pagoda3-store/{pid}/{streamed_key}/",
            label="Explore in pagoda3",
            # Origin-shared with the pagoda3 window → its copilot proxies through ABA.
            set_local_storage={"p3-agent-proxy": "/pagoda3-api"},
            # No local store_path: the store is STREAMED, not materialized, so the
            # single-file download (which needs the whole tree) is unavailable until
            # a mirror brings it home (the download endpoint returns a clean 409).
        )

    src = _resolve_source(node, pid, set_phase)
    if not src.exists():
        raise _source_not_found(node)   # honesty bridge: entity-remote facts → remote wording

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

    # W3.4: lstar runs in the project SESSION env (has lstar-sc), reached through
    # the runtime activation (a mount base's python isn't bare-execable). Resolve the
    # R bridge + the cache key from that same session lstar-sc (so a pack lstar
    # upgrade rederives). Pack-less deploys resolve to sys.executable exactly as before.
    _rs = _rscript(pid)
    _cache_ver = _launcher_version(pid)
    if suffix == _STORE_SUFFIX:
        # Already a store — nothing to derive; symlink it into the served dir
        # (copy only if it lives outside the project). No ensure_derived cache:
        # the store IS the source, so there's nothing to key on or rebuild —
        # which is also why this lane carries its own pre-0.2.2 basis repair
        # instead of getting one from a cache-version bump.
        store = _serve_native_store(src, cache_dir, out_name, root, set_phase,
                                    pid=pid)
    else:
        base_convert = _unzip_store if suffix == _ZIP_SUFFIX else _convert_any
        def convert(s: Path, o: Path) -> None:  # bind set_phase + interpreters
            if base_convert is _convert_any:
                _convert_any(s, o, set_phase, pid=pid, rscript=_rs)
            else:
                base_convert(s, o, set_phase, pid=pid)
        store = ensure_derived(src, cache_dir, out_name, _cache_ver, convert)

    return LaunchResult(
        url=f"/pagoda3/?store=/pagoda3-store/{pid}/{store.name}/",
        label="Explore in pagoda3",
        # Origin-shared with the pagoda3 window → its copilot proxies through ABA.
        set_local_storage={"p3-agent-proxy": "/pagoda3-api"},
        # The prepared .lstar.zarr on disk — the download endpoint packs THIS
        # (cache-shared with viewing) into lstar's single-file STORED .lstar.zarr.zip.
        store_path=str(store),
        download_packer=lambda sd, d: _pack_download(sd, d, pid=pid),
    )


register_launcher("pagoda3_launcher", launch)
