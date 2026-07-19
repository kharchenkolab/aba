"""Detached lane on a REAL (mock) cluster — cross-OS e2e (misc/detached_compute.md S2).

The controller (this machine) shares NOTHING with the node (linux docker
fixture): code ships as a CAS payload, runs under the node/env python, and
results come back over the data plane. Covers the bare job (honest
node-system grade) and the env job (lazy platform re-lock + realize-on-site).

OPT-IN (slow, needs docker — orbstack on mac):
    ABA_WEFT_CLUSTER=1 python -m pytest tests/test_detached_cluster.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("ABA_WEFT_CLUSTER"),
                                reason="set ABA_WEFT_CLUSTER=1 (docker) for the "
                                       "detached-lane cluster e2e")

_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _sh(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800, **kw)


def _weft_repo() -> Path:
    return Path(os.environ.get("WEFT_SRC", Path.home() / "aba" / "weft"))


@pytest.fixture(scope="module")
def cluster():
    if os.system("docker info >/dev/null 2>&1") != 0:
        pytest.skip("docker not available")
    keydir = Path(tempfile.mkdtemp(prefix="aba_detc_keys_"))
    build = _sh("sh", str(_weft_repo() / "tests/fixtures/slurm/build.sh"), str(keydir))
    if build.returncode != 0:
        pytest.skip(f"cannot build slurm fixture: {build.stderr[-300:]}")
    name = f"aba-detcluster-{uuid.uuid4().hex[:8]}"
    run = _sh("docker", "run", "-d", "--rm", "--name", name,
              "--device", "/dev/fuse", "--cap-add", "SYS_ADMIN",
              "--hostname", "weftslurm", "-p", "127.0.0.1::22",
              "weft-test-slurm")
    assert run.returncode == 0, run.stderr
    port = _sh("docker", "port", name, "22").stdout.strip().rsplit(":", 1)[-1]
    ssh_opts = ["-i", str(keydir / "id_ed25519"), "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes"]
    for _ in range(120):
        ok = _sh("ssh", *ssh_opts, "-o", "BatchMode=yes", "-p", port,
                 "physicist@127.0.0.1", "sinfo -h -o %a 2>/dev/null | head -1")
        if ok.returncode == 0 and "up" in ok.stdout.lower():
            break
        time.sleep(0.5)
    else:
        _sh("docker", "rm", "-f", name)
        pytest.skip("slurm fixture never became ready")
    yield {"port": int(port), "ssh_opts": ssh_opts}
    _sh("docker", "rm", "-f", name)


@pytest.fixture(scope="module")
def lane(cluster):
    _t = tempfile.mkdtemp(prefix="aba_detc_")
    os.environ["ABA_HOME"] = _t
    os.environ.setdefault("ABA_DB_PATH", os.path.join(_t, "d.db"))
    os.environ.setdefault("ABA_RUNTIME_DIR", os.path.join(_t, "rt"))
    from core.graph._schema import init_db
    init_db()
    import core.projects as _p
    _p.SINGLE = True
    from core.compute import adapter as ad
    st = ad.configure()
    assert st["ok"], st["detail"]
    c = ad.get_compute()
    c.sync_call("register_site", "hpc", "slurm",
                {"root": "/home/physicist/.weft", "host": "127.0.0.1",
                 "port": cluster["port"], "user": "physicist",
                 "ssh_opts": cluster["ssh_opts"]})
    yield c
    from core.compute import adapter as ad2
    ad2.shutdown()


def _wait_job(job_id, site="hpc", n=400, project_id="single"):
    from core.jobs.weft_submitter import WeftSubmitter
    from core.graph.jobs import get_job
    sub = WeftSubmitter(site=site)
    for _ in range(n):
        res = sub.poll(get_job(job_id, project_id=project_id))
        if res is not None:
            return res, sub
        time.sleep(1)
    raise AssertionError("job never finished")


def test_bare_job_cross_os(lane):
    from core.jobs.submit import submit_python_job
    code = ("import json, platform\n"
            "vals = [i*i for i in range(1, 101)]\n"
            "open('out.json','w').write(json.dumps({'sum': sum(vals)}))\n"
            "print('SUM', sum(vals), platform.system())\n")
    job = submit_python_job(code, title="detc bare", focus_entity_id=None,
                            project_id="single", site="hpc",
                            estimate={"cores": 1}, timeout_s=300)
    res, sub = _wait_job(job["id"])
    assert res["status"] == "ok" and "SUM 338350 Linux" in res["stdout"]
    assert res["compute"]["env_grade"] == "node-system"
    from core.graph.jobs import get_job
    local = sub._run_dir(get_job(job["id"], project_id="single")) / "out.json"
    assert json.loads(local.read_text())["sum"] == 338350


def test_env_job_relock_and_realize(lane):
    from core.compute import named_envs
    from core.jobs.submit import submit_python_job
    named_envs.create("single", "detc-tools", packages=["click"])
    job = submit_python_job("import click, platform; print('ENVRUN', platform.system())",
                            title="detc env", focus_entity_id=None,
                            project_id="single", site="hpc", env="detc-tools",
                            estimate={"cores": 1}, timeout_s=300)
    res, _sub = _wait_job(job["id"])
    assert res["status"] == "ok" and "ENVRUN Linux" in res["stdout"]
    from core.graph.jobs import get_job
    p = get_job(job["id"], project_id="single")["params"]
    assert p.get("env_id")                          # ran IN the env
    row = named_envs.resolve("single", "detc-tools")
    plats = row.get("platforms") or []
    assert any(pl.startswith("linux-") for pl in plats)   # re-locked for the site
