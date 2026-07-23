#!/usr/bin/env python
"""Live environment-promotion checklist (the env-resolution campaign's
deployment gate). Runs against a DEPLOYED backend + real substrate — no LLM,
no HTTP: it drives the same lane entry points the agent's tools call, on a
scratch project, and asserts the promotion chain end to end:

    isolated env (with a SYSTEM library) → promote → bare run lands in it →
    installer/probe target it → layers report shows it → mismatch refused →
    reset restores the default.

Usage:
    ABA_HOME=<deployment home> <deployment python> regtest/harness/env_check.py
        [--r]        also run the R-lane journey (heavier solve)
        [--quick]    skip the kernel-based bare-run check (uses the one-shot lane)
        [--failures] the failure wing: error notes carry the substrate's
                     diagnosis, stay request-scoped, and the system-library
                     remedy never fires on a resolve-stage failure
        [--keep]     keep the scratch project + envs for inspection

Exit 0 = every check passed; 1 = something failed (each check prints its own
[ok]/[FAIL] line, so a red run names the broken link)."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "ok" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail and not ok else ""),
          flush=True)
    if not ok:
        _FAILS.append(label)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--r", action="store_true", help="also run the R journey")
    ap.add_argument("--failures", action="store_true",
                    help="run the failure-honesty wing (pays for real failed "
                         "lookups; deterministic on any topology)")
    ap.add_argument("--remote", default=None, metavar="SITE",
                    help="run the remote addressing wing against SITE (a "
                         "configured non-local site; an ssh-loopback site "
                         "with a separate workspace root also qualifies — "
                         "it deliberately breaks the shared-FS accident "
                         "that masks F3). ARMED: unreachable site FAILS.")
    ap.add_argument("--quick", action="store_true",
                    help="skip the kernel bare-run (one-shot lane only)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch project")
    args = ap.parse_args()

    from core.compute import adapter, named_envs
    from core.compute.errors import ComputeError
    from core.config import PROJECTS_DIR
    from core import projects

    adapter.configure()          # idempotent process-wide substrate wiring
    try:
        adapter.get_compute()
    except Exception as e:  # noqa: BLE001
        print(f"ABORT: compute substrate unavailable ({e}) — this checklist "
              f"needs a live deployment", flush=True)
        return 1

    pid = f"prj_envcheck_{uuid.uuid4().hex[:8]}"
    print(f"[env_check] scratch project {pid}", flush=True)

    try:
        projects.set_current(pid)
        # ── 1. isolated env carrying a SYSTEM library ────────────────────────
        # zlib is a shared C library, not a python package — exactly the class
        # the session overlay can never carry. Solving it into the isolated
        # env is the capability the whole campaign is about. Driven through
        # the TOOL impl, not named_envs directly: agent-reachable claims must
        # be proven at the surface the agent can reach (the conda_packages
        # passthrough existed at the function layer for a week while the tool
        # could not express it — D1).
        from content.bio.tools import make_isolated_env as _mk_env
        from content.bio.tools import set_active_env as _set_env
        res = _mk_env({"name": "envchk", "packages": [],
                       "conda_packages": ["zlib"]}, {"thread_id": f"ec-{pid}"})
        check("isolated env solves with a system library (via the tool)",
              bool(res.get("env_id")), str(res)[:300])

        # ── 2. promotion + resolution ────────────────────────────────────────
        _pr = _set_env({"name": "envchk", "language": "python"},
                       {"thread_id": f"ec-{pid}"})
        check("promotion binds the python slot (via the tool)",
              named_envs.get_active(pid, "python") == "envchk",
              str(_pr)[:200])
        check("resolve_env follows the pointer",
              named_envs.resolve_env(pid, "python") == "envchk")
        check("resolve_env cross-language isolation",
              named_envs.resolve_env(pid, "r") is None)

        # ── 3. lane wiring, live ─────────────────────────────────────────────
        from content.bio.tools.run_exec import bg_submit_kwargs
        check("background submit carries the promoted env",
              bg_submit_kwargs({}, pid)["env"] == "envchk")
        check("explicit env still wins over the pointer",
              bg_submit_kwargs({"env": "default"}, pid)["env"] is None)

        # ── 4. the promoted env is real: run in it, see the system lib ──────
        # BOTH facts come from INSIDE the env. Globbing the prefix from here
        # only works when the env is a materialized directory: on a
        # squashfs-image deployment the prefix is a mountpoint that is live
        # solely within the run's mount namespace, so a host-side glob finds
        # nothing and reports a present library as absent (measured 2026-07-22:
        # libz.so sits in the image while the outer glob saw an empty dir).
        r = named_envs.run_in(pid, "envchk",
                              "import sys, glob, os\n"
                              "print('PFX=' + sys.prefix)\n"
                              "print('LIBZ=' + str(bool("
                              "glob.glob(os.path.join(sys.prefix,'lib','libz*'))"
                              " or glob.glob(os.path.join("
                              "sys.prefix,'Library','bin','zlib*')))))",
                              timeout_s=900)
        out = (r.get("stdout") or "").splitlines()
        pfx = next((ln[len("PFX="):] for ln in out if ln.startswith("PFX=")), "")
        check("one-shot run executes inside the promoted env",
              bool(r.get("ok")) and bool(pfx), str(r.get("stderr") or "")[-300:])
        if pfx:
            check("the system library is present in the promoted prefix",
                  "LIBZ=True" in out,
                  f"no libz* under {pfx}/lib (as seen from inside the env)")

        if not args.quick:
            # ── 5. BARE run_python (no env=) lands in the promoted env ───────
            projects.set_current(pid)
            from content.bio.tools.run_exec import run_python
            out = run_python({"code": "import sys; print('BARE_PFX=' + sys.prefix)"},
                             {"thread_id": f"envchk-{pid}"})
            stdout = out.get("stdout") or ""
            bare = next((ln[len("BARE_PFX="):] for ln in stdout.splitlines()
                         if ln.startswith("BARE_PFX=")), "")
            check("BARE run_python executes inside the promoted env",
                  bare != "" and bare == pfx,
                  f"bare={bare!r} promoted={pfx!r} status={out.get('status')} "
                  f"note={str(out.get('note') or out.get('error') or '')[:200]}")

        # ── 6. the probe answers about the promoted env ──────────────────────
        from core.exec.env_integrity import python_package_status
        st = python_package_status("ipykernel", project_id=pid)
        check("package probe targets the promoted env",
              st.get("env") == "envchk" and st.get("tier") == "isolated"
              and st.get("loads") is True,
              f"tier={st.get('tier')} env={st.get('env')} loads={st.get('loads')} "
              f"err={str(st.get('error') or '')[:200]}")

        # ── 7. the layers report shows the isolated tier ─────────────────────
        from core.exec.env_integrity import env_layers
        layers = env_layers(pid)
        iso = [l for l in layers["python"]["layers"]
               if l.get("tier") == "isolated" and l.get("name") == "envchk"]
        check("layers report lists the isolated env", len(iso) == 1)

        # ── 8. refusals stay loud ────────────────────────────────────────────
        try:
            named_envs.set_active(pid, "envchk", "r")
            check("cross-language binding refused", False, "no error raised")
        except ComputeError as e:
            check("cross-language binding refused",
                  e.code == "env.language_mismatch", e.code)
        try:
            named_envs.set_active(pid, "ghost", "python")
            check("unknown env refused", False, "no error raised")
        except ComputeError as e:
            check("unknown env refused", e.code == "unknown_env", e.code)

        # ── 9. reset restores the default ────────────────────────────────────
        named_envs.set_active(pid, "default", "python")
        check("reset restores default resolution",
              named_envs.resolve_env(pid, "python") is None)
        check("post-reset background submit uses the default session",
              bg_submit_kwargs({}, pid)["env"] is None)

        # ── 10. latency budget: re-asking is a lookup, not an interpreter ───
        # The instrument whose absence let a 69s no-op ship (live 2026-07-22):
        # a capability request whose answer is already PROVEN for the current
        # identity must be near-free. Placed AFTER the reset so the DEFAULT
        # session serves it (with a promoted env the pointer would route the
        # request into the named lane and mutate the scratch env). First ask
        # pays one real probe subprocess and memoizes; the repeat must come
        # back inside the budget. ARMED: the first ask must itself register
        # measurable work — a 0.0s pair means the timer measured nothing.
        import time as _time
        projects.set_current(pid)
        from content.bio.tools.discovery import ensure_capability as _ecp
        _t0 = _time.monotonic()
        _e1 = _ecp({"name": "numpy"}, {"thread_id": f"perf-{pid}"})
        _t1 = _time.monotonic()
        _e2 = _ecp({"name": "numpy"}, {"thread_id": f"perf-{pid}"})
        _t2 = _time.monotonic()
        _first, _second = _t1 - _t0, _t2 - _t1
        check("perf: probe pair measured real work (armed)",
              _e1.get("status") == "ready" and _first > 0.05,
              f"first={_first:.2f}s status={_e1.get('status')} "
              f"note={str(_e1.get('note') or '')[:120]}")
        check("perf: repeat capability ask is memoized (< 2s budget)",
              _e2.get("status") == "ready" and _second < 2.0
              and _second < _first,
              f"first={_first:.2f}s second={_second:.2f}s")

        # ── 10b. first pypi install on a FRESH session: overlay, not clone ──
        # weft's perf round regated the overlay lane on WRITE-NEED: a
        # pypi-only add rides a pylib layer over the base — zero clone. Field
        # before/after: 50.5s (10.2s clone + 24.2s probe + install) → ~1.3s.
        # Budget 15s (generous for cold pip); the entry-count ceiling is the
        # structural half — a clone would lay down ~55k files.
        import pathlib as _pl
        from core.compute import project_env as _pe2
        _t3 = _time.monotonic()
        _ins = _pe2.install(pid, "python", ["six"], eco="pypi",
                            verify={"import": ["six"]})
        _dt3 = _time.monotonic() - _t3
        _sid3 = str(_ins.get("session_id") or "")
        _sd = (_pl.Path.home() / ".aba/weft/site-local/sessions" / _sid3)
        _n3 = sum(1 for _ in _sd.rglob("*")) if (_sid3 and _sd.exists()) else 0
        check("perf: first pypi install is overlay-fast (< 15s, no clone)",
              _dt3 < 15.0 and _n3 < 5000,
              f"install={_dt3:.1f}s session_entries={_n3}")

        # ── failure wing (--failures): honesty when things break ────────────
        # Deterministic on ANY topology: a github install of a repo that does
        # not exist fails at fetch/resolve everywhere. The contract: the note
        # carries the substrate's own diagnosis, stays scoped to ITS request,
        # and never gets the system-library lecture (resolve-stage failure).
        if args.failures:
            projects.set_current(pid)
            # The tool entry correctly routes an UNCATALOGUED name to the
            # catalog's not_found path — the lanes under test here are the R
            # install lanes themselves, driven with a provisioning record the
            # way a catalogued/proposed capability drives them.
            from content.bio.tools.discovery import _ensure_r_via_session
            r1 = _ensure_r_via_session(
                {"name": "envcheck-missing-one", "provisioning": {"r": {
                    "source": "github",
                    "package": "aba-envcheck/definitely-missing-repo-one"}}},
                {}, None, "envcheck-missing-one")
            n1 = str(r1.get("note") or "")
            check("failed install reports an error",
                  r1.get("status") == "error", str(r1)[:200])
            check("failure note carries the substrate diagnosis (not a summary)",
                  len(n1) > 120 and any(s in n1.lower() for s in
                                        ("cannot open", "404", "url",
                                         "failed to install", "fail")),
                  n1[:250])
            check("resolve-stage failure gets NO system-library lecture",
                  "missing SYSTEM library" not in n1, n1[-250:])
            r2 = _ensure_r_via_session(
                {"name": "envcheck-missing-two", "provisioning": {"r": {
                    "source": "github",
                    "package": "aba-envcheck/definitely-missing-repo-two"}}},
                {}, None, "envcheck-missing-two")
            n2 = str(r2.get("note") or "")
            check("diagnosis is request-scoped (no cross-request quote)",
                  "definitely-missing-repo-one" not in n2, n2[:250])

        # ── remote addressing wing (--remote SITE): F3 live, unmasked ───────
        # misc/paths.md: a site-targeted kernel's outputs must be addressable
        # — relative-name registration binds the RIGHT bytes with the durable
        # run_key captured, and find_files answers locality/durability
        # honestly. The pilot's shared-FS topology masks this class, so the
        # wing requires a genuinely non-local site (or an ssh-loopback site
        # with a separate workspace root). ARMED: a missing site FAILS.
        if args.remote:
            projects.set_current(pid)
            from content.bio.tools.run_exec import run_python
            _rw = run_python({"code": "open('rw_marker.bin','w').write('RW1')"
                                      "\nprint('WROTE')",
                              "site": args.remote},
                             {"thread_id": f"rw-{pid}"})
            check(f"remote wing: kernel ran on site {args.remote!r}",
                  "WROTE" in (_rw.get("stdout") or ""),
                  str(_rw.get("note") or _rw.get("error") or _rw)[:300])
            from content.bio.tools.curation import register_dataset_tool
            _reg = register_dataset_tool({"path": "rw_marker.bin",
                                          "title": "rw marker"},
                                         {"thread_id": f"rw-{pid}"})
            check("remote wing: relative-name registration succeeds",
                  bool(_reg.get("dataset_id")), str(_reg)[:300])
            if _reg.get("dataset_id"):
                from core.graph.entities import get_entity as _ge
                _md = (_ge(_reg["dataset_id"]) or {}).get("metadata") or {}
                check("remote wing: durable run_key captured (F3's handle)",
                      bool(_md.get("run_key")), str(_md)[:250])
            from content.bio.project_locate import locate_project_files
            _ff = locate_project_files("rw_marker.bin", limit=5,
                                       ctx={"thread_id": f"rw-{pid}"})
            _hits = _ff.get("matches", [])
            check("remote wing: find_files answers durability on every hit",
                  bool(_hits) and all(h.get("durability") or
                                      h.get("locality") == "remote"
                                      for h in _hits), str(_hits)[:300])

        # ── R journey (--r): the campaign's flagship case ────────────────────
        if args.r:
            from content.bio.tools import make_isolated_env as _mk_env2
            from content.bio.tools import set_active_env as _set_env2
            res = _mk_env2({"name": "renvchk", "language": "r", "packages": [],
                            "conda_packages": ["zlib"]},
                           {"thread_id": f"ec-r-{pid}"})
            check("isolated R env solves with a system library (via the tool)",
                  bool(res.get("env_id")), str(res)[:300])
            _set_env2({"name": "renvchk", "language": "r"},
                      {"thread_id": f"ec-r-{pid}"})
            check("R promotion binds the r slot",
                  named_envs.resolve_env(pid, "r") == "renvchk")

            # ── extend wing: ensure_capability(env=…) at the TOOL surface ───
            # The env= lane's repaired contract, live: spec fidelity through
            # the dispatch, verify-or-refuse at the named lane. Positive: a
            # tiny pure-R registry package extends the env and is VERIFIED
            # there before ready. Negative: a github+subdir record for a repo
            # that cannot exist — the composed grammar (owner/repo/subdir@ref)
            # must reach the substrate (its error quotes the spec), and no
            # ready without a loadable artifact.
            from content.bio.tools.discovery import ensure_capability as _ec
            okx = _ec({"name": "praise", "env": "renvchk"},
                      {"thread_id": f"ec-r-{pid}"})
            check("env= install is ready with HONEST enforcement facts",
                  okx.get("status") == "ready"
                  and (bool(okx.get("verified"))
                       or okx.get("verification") == "deferred"),
                  str(okx)[:300])
            bad = _ec({"name": "envcheck-ghost", "env": "renvchk",
                       "source": "github",
                       "package": "aba-envcheck/definitely-missing",
                       "subdir": "R", "ref": "main"},
                      {"thread_id": f"ec-r-{pid}"})
            _btxt = str(bad)
            # the substrate's resolver splits the spec (repo@ref resolved,
            # subdir applied after), so its 404 quotes repo@ref — that pair
            # surviving the dispatch is the live observable here; subdir
            # composition is pinned hermetically (test_cap_request) and at
            # agent level (r_github_subdir scenario, a real subdir repo).
            check("env= github package@ref survives to the substrate",
                  "aba-envcheck/definitely-missing" in _btxt
                  and "@main" in _btxt, _btxt[:300])
            check("env= failure refuses ready",
                  bad.get("status") != "ready", _btxt[:200])
            rr = named_envs.run_in(pid, "renvchk",
                                   "cat('R_PFX=', R.home(), '\\n', sep='')",
                                   timeout_s=1800)
            check("one-shot R run executes inside the promoted R env",
                  bool(rr.get("ok")) and "R_PFX=" in (rr.get("stdout") or ""),
                  str(rr.get("stderr") or "")[-300:])
            projects.set_current(pid)
            from content.bio.tools.run_exec import run_r
            tidr = f"envchk-r-{pid}"
            outr = run_r({"code": "cat('BARE_R=', R.home(), '\\n', sep='')"},
                         {"thread_id": tidr})
            check("BARE run_r executes inside the promoted R env",
                  "BARE_R=" in (outr.get("stdout") or "")
                  and outr.get("env") == "renvchk"
                  and not outr.get("kernel_warning"),
                  f"mode={outr.get('execution_mode')} env={outr.get('env')} "
                  f"warn={str(outr.get('kernel_warning') or '')[:150]} "
                  f"note={str(outr.get('note') or outr.get('error') or '')[:200]}")
            # THE point of kernel parity: state persists across bare calls.
            run_r({"code": "x <- 41"}, {"thread_id": tidr})
            out2 = run_r({"code": "cat('STATE=', x + 1, '\\n', sep='')"},
                         {"thread_id": tidr})
            check("bare run_r state persists across calls (persistent kernel)",
                  "STATE=42" in (out2.get("stdout") or ""),
                  f"mode={out2.get('execution_mode')} "
                  f"warn={str(out2.get('kernel_warning') or '')[:150]} "
                  f"stderr={str(out2.get('stderr') or '')[:200]}")
            named_envs.set_active(pid, "default", "r")

    finally:
        if not args.keep:
            try:
                shutil.rmtree(PROJECTS_DIR / pid, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    n_fail = len(_FAILS)
    print(f"=== env_check: {'PASS' if not n_fail else 'FAIL'} "
          f"({n_fail} failed) ===", flush=True)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
