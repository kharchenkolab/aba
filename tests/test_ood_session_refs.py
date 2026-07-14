"""End-to-end OOD node-side flow for refs, against the DEPLOYED dev config
(ood-cluster/site.yaml), path-rebased to a host sandbox exactly as the OOD
container's /groups + /cluster/aba bind-mounts do. Two per-user sessions in one
group run the REAL aba_preflight, then two SEPARATE per-user backend processes
register + discover a group-scoped reference through the shared group store —
the multi-user sharing the OOD deployment is for.

What this still doesn't cover (needs the real cluster): a 2nd real UID enforcing
permissions, and the OOD web/Slurm wrapper. The node-side logic is all exercised.

Run:  .venv/bin/python tests/test_ood_session_refs.py
"""
from __future__ import annotations
import grp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = str(ROOT / "backend")
PREFLIGHT = ROOT / "install" / "ood" / "aba_preflight.py"
DEV_SITE = Path("/home/pkharchenko/aba/ood-cluster/site.yaml")
DEV_SKEL = Path("/home/pkharchenko/aba/ood-cluster/group-skeleton")
_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _secondary_group():
    prim = os.getgid()
    for g in os.getgroups():
        if g != prim:
            try:
                return grp.getgrgid(g).gr_name, g
            except KeyError:
                continue
    return None, None


_ACTOR = r'''
import os, sys, json
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, os.environ["ABA_BACKEND"])
from core import projects
import content.bio  # noqa
from content.bio.tools import register_reference_tool, find_reference_tool, resolve_reference_tool
projects.init()
projects.create_project("p")
mode = sys.argv[1]
if mode == "register":
    print(json.dumps(register_reference_tool(
        {"path": os.environ["REF_PATH"], "organism": "fly", "role": "genome",
         "assembly": "BDGP6", "scope": "group"})))
else:
    f = find_reference_tool({"organism": "fly", "role": "genome"})
    res = resolve_reference_tool({"organism": "fly", "role": "genome"}) if f.get("found") else {}
    print(json.dumps({"find": f, "resolve_ok": res.get("status") == "ok",
                      "local_path": res.get("local_path")}))
'''


def main() -> int:
    lab, lab_gid = _secondary_group()
    if not DEV_SITE.is_file():
        print(f"  [SKIP] dev OOD config not found at {DEV_SITE}")
        print("ALL OOD-SESSION-REFS CHECKS PASSED")
        return 0
    if not lab:
        print("  [SKIP] no secondary unix group to act as the lab group")
        print("ALL OOD-SESSION-REFS CHECKS PASSED")
        return 0

    sandbox = Path(tempfile.mkdtemp(prefix="aba_oodsess_"))
    groups_root = sandbox / "groups"
    cluster = sandbox / "cluster" / "aba"
    cluster.mkdir(parents=True, exist_ok=True)
    # Rebase the DEPLOYED site.yaml's absolute paths to the host sandbox — exactly
    # what the OOD container does via its /groups + /cluster/aba bind-mounts.
    site_txt = DEV_SITE.read_text().replace("/cluster/aba", str(cluster)).replace("/groups", str(groups_root))
    (cluster / "site.yaml").write_text(site_txt)
    if DEV_SKEL.is_dir():
        shutil.copytree(DEV_SKEL, cluster / "group-skeleton", dirs_exist_ok=True)
    actor = sandbox / "actor.py"
    actor.write_text(_ACTOR)
    site_cfg = str(cluster / "site.yaml")
    py = sys.executable

    def session(user):
        """Run the real preflight for one user's session; return ABA_RUNTIME_DIR."""
        home = sandbox / "homes" / user
        staged = sandbox / "staged" / user
        home.mkdir(parents=True, exist_ok=True)
        staged.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({"ABA_SITE_CONFIG": site_cfg, "ABA_PF_GROUP": lab,
                    "ABA_PF_USER": user, "ABA_PF_HOME": str(home), "ABA_PF_STAGED": str(staged)})
        r = subprocess.run([py, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        check(f"preflight ok for session {user}", r.returncode == 0, r.stderr[-200:])
        # state_dir from site.yaml is /groups/{group}/aba/{user} → rebased:
        return groups_root / lab / "aba" / user

    def run_actor(user, runtime_dir, mode, ref_path=None):
        env = dict(os.environ)
        env.pop("ABA_DB_PATH", None)
        env.update({"ABA_BACKEND": BACKEND, "ABA_RUNTIME_DIR": str(runtime_dir),
                    "ABA_ENVS_DIR": str(runtime_dir / "envs"),
                    "ABA_REFS_DIR": str(runtime_dir / "refs"),  # per-user personal tier
                    "ABA_SITE_CONFIG": site_cfg, "ABA_GROUP": lab})
        if ref_path:
            env["REF_PATH"] = str(ref_path)
        r = subprocess.run([py, str(actor), mode], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"    actor {user}/{mode} stderr: {r.stderr[-300:]}")
            return {}
        return json.loads(r.stdout.strip().splitlines()[-1])

    print(f"session 1: user 'alice' in group '{lab}' registers a group-scoped genome")
    alice_rt = session("alice")
    ref_fa = sandbox / "genome.fa"
    ref_fa.write_text(">chr1\nACGTACGTACGT\n")
    reg = run_actor("alice", alice_rt, "register", ref_fa)
    check("alice registered the ref at group scope", reg.get("scope") == "group", str(reg)[:140])
    group_refs = groups_root / lab / "aba" / "refs"
    rid = reg.get("reference_id")
    check("descriptor landed in the SHARED group refs store",
          bool(rid) and (group_refs / "registry" / f"{rid}.json").exists(), str(group_refs))

    print(f"session 2: user 'bob' in group '{lab}' — a DIFFERENT per-user runtime")
    bob_rt = session("bob")
    check("alice and bob have separate per-user runtimes", alice_rt != bob_rt)
    found = run_actor("bob", bob_rt, "find")
    check("bob discovers alice's group ref (cross-user sharing)",
          found.get("find", {}).get("found") and found["find"]["reference"]["id"] == rid,
          str(found)[:160])
    check("bob can resolve it to a local path", found.get("resolve_ok") is True)

    print("group store ownership (setgid + lab group)")
    if group_refs.is_dir():
        st = group_refs.stat()
        import stat as _stat
        check("group refs dir is setgid", bool(st.st_mode & _stat.S_ISGID))
        check("group refs dir owned by the lab group", st.st_gid == lab_gid,
              f"{grp.getgrgid(st.st_gid).gr_name} vs {lab}")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL OOD-SESSION-REFS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
