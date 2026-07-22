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
        # ── 1. isolated env carrying a SYSTEM library ────────────────────────
        # zlib is a shared C library, not a python package — exactly the class
        # the session overlay can never carry. Solving it into the isolated
        # env is the capability the whole campaign is about.
        res = named_envs.create(pid, "envchk", packages=[],
                                conda_packages=["zlib"])
        check("isolated env solves with a system library",
              bool(res.get("env_id")), str(res))

        # ── 2. promotion + resolution ────────────────────────────────────────
        named_envs.set_active(pid, "envchk", "python")
        check("promotion binds the python slot",
              named_envs.get_active(pid, "python") == "envchk")
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

        # ── R journey (--r): the campaign's flagship case ────────────────────
        if args.r:
            res = named_envs.create(pid, "renvchk", language="r", packages=[],
                                    conda_packages=["zlib"])
            check("isolated R env solves with a system library",
                  bool(res.get("env_id")), str(res))
            named_envs.set_active(pid, "renvchk", "r")
            check("R promotion binds the r slot",
                  named_envs.resolve_env(pid, "r") == "renvchk")
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
