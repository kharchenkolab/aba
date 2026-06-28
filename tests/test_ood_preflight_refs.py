"""OOD preflight ↔ refs wiring (the local-testable slice of the OOD path).
Runs the REAL install/ood/aba_preflight.py against a local site.yaml of the
VBC shape and checks it creates the GROUP refs dir group-writable + setgid,
owned by the lab group (chgrp), with new files inheriting the group — and that
the backend's _tier_roots resolves the SAME path. Uses one of THIS user's
secondary unix groups as the stand-in lab group.

What still needs the real cluster: cross-UID enforcement (a 2nd real user), and
the live OnDemand/Slurm session launch. The wiring logic + perms are all here.

Run:  .venv/bin/python tests/test_ood_preflight_refs.py
"""
from __future__ import annotations
import grp
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "install" / "ood" / "aba_preflight.py"
_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _secondary_group():
    """A unix group this user belongs to that ISN'T the primary — so chgrp to it
    actually changes ownership (and is permitted, since we're a member)."""
    prim = os.getgid()
    for g in os.getgroups():
        if g != prim:
            try:
                return grp.getgrgid(g).gr_name, g
            except KeyError:
                continue
    return None, None


def main() -> int:
    lab, lab_gid = _secondary_group()
    _tmp = tempfile.mkdtemp(prefix="aba_oodrefs_")
    user = os.environ.get("USER") or "tester"
    home = str(Path(_tmp) / "home")
    staged = str(Path(_tmp) / "staged")
    Path(home).mkdir(parents=True, exist_ok=True)
    Path(staged).mkdir(parents=True, exist_ok=True)

    if not lab:
        print("  [SKIP] no secondary unix group to simulate a lab group")
        print("ALL OOD-PREFLIGHT-REFS CHECKS PASSED")
        return 0

    site = Path(_tmp) / "site.yaml"
    site.write_text(
        "site:\n  name: TestSite\n"
        "scopes:\n"
        "  group:\n    enabled: true\n"
        f"    root_path: {_tmp}/groups/{{group}}/aba\n"
        "  user:\n"
        f"    state_dir: {_tmp}/groups/{{group}}/aba/users/{{user}}\n"
        "refs:\n"
        f"  group: {_tmp}/groups/{{group}}/aba/refs\n")

    env = dict(os.environ)
    env.update({"ABA_SITE_CONFIG": str(site), "ABA_PF_GROUP": lab,
                "ABA_PF_USER": user, "ABA_PF_HOME": home, "ABA_PF_STAGED": staged})
    r = subprocess.run([sys.executable, str(PREFLIGHT)], env=env,
                       capture_output=True, text=True)
    print(f"  preflight rc={r.returncode}: {r.stdout.strip()[:140]}")
    check("preflight succeeded (not blocked)", r.returncode == 0, r.stderr[-300:])

    refs_dir = Path(_tmp) / "groups" / lab / "aba" / "refs"
    check("group refs dir created", refs_dir.is_dir(), str(refs_dir))
    if refs_dir.is_dir():
        st = refs_dir.stat()
        import stat as _stat
        check("setgid bit set (children inherit the group)", bool(st.st_mode & _stat.S_ISGID))
        check("group-writable (mode 2775)", (st.st_mode & 0o2775) == 0o2775, oct(st.st_mode & 0o7777))
        check("owned by the lab group (chgrp worked)", st.st_gid == lab_gid,
              f"{grp.getgrgid(st.st_gid).gr_name} vs {lab}")
        # a new file created under it inherits the lab group (setgid in action)
        f = refs_dir / "probe"
        f.write_text("x")
        check("new file inherits the lab group (setgid)", f.stat().st_gid == lab_gid)

    print("  status.yaml reports the refs tier")
    import yaml
    status = yaml.safe_load((Path(staged) / "status.yaml").read_text())
    check("status.scopes.refs == ok",
          (status.get("scopes", {}).get("refs", {}).get("state")) == "ok", str(status.get("scopes", {}).get("refs")))

    print("  backend _tier_roots resolves the SAME group refs path (preflight ↔ backend agree)")
    os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
    os.environ.setdefault("ABA_ENVS_DIR", str(Path(_tmp) / "envs"))
    os.environ.setdefault("ABA_REFS_DIR", str(Path(_tmp) / "personal"))
    sys.path.insert(0, str(ROOT / "backend"))
    from core.data.refstore import _tier_roots  # noqa: E402
    tiers = dict(_tier_roots({"ABA_SITE_CONFIG": str(site), "ABA_GROUP": lab,
                              "USER": user, "HOME": home}))
    check("backend group tier == preflight-created refs dir", tiers.get("group") == refs_dir,
          str(tiers.get("group")))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL OOD-PREFLIGHT-REFS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
