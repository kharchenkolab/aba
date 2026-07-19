"""Datasets on a REAL (mock) Slurm cluster — weft's dockerized fixture,
driven through aba's adapter + core/data/datasets (misc/datasets2.md v2):

  * durable-home registration ON the cluster (fingerprint site-side,
    zero copy, bytes never touch the controller)
  * a slurm task consuming the ref via symlink staging (0-byte plan)
  * drift on the cluster → ensure_ref fences pre-submit; a forced stale
    submit fails data.verify_failed and translates to plain language

OPT-IN (slow, needs docker — orbstack on mac):
    ABA_WEFT_CLUSTER=1 python -m pytest tests/test_datasets_cluster.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_dscluster_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = [
    pytest.mark.platform,
    pytest.mark.skipif(not os.environ.get("ABA_WEFT_CLUSTER"),
                       reason="set ABA_WEFT_CLUSTER=1 (docker) for the "
                              "mock-cluster dataset round trip"),
]

DATA = "/home/physicist/groups/lab/atlas"     # the "shared share" ON the cluster


def _sh(*args, timeout=600):
    return subprocess.run(list(args), capture_output=True, text=True,
                          timeout=timeout)


def _weft_repo() -> Path:
    import weft
    return Path(weft.__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cluster():
    if os.system("docker info >/dev/null 2>&1") != 0:
        pytest.skip("docker not available")
    keydir = Path(tempfile.mkdtemp(prefix="aba_dsc_keys_"))
    build = _sh("sh", str(_weft_repo() / "tests/fixtures/slurm/build.sh"),
                str(keydir))
    if build.returncode != 0:
        pytest.skip(f"cannot build slurm fixture: {build.stderr[-300:]}")
    name = f"aba-dscluster-{uuid.uuid4().hex[:8]}"
    run = _sh("docker", "run", "-d", "--rm", "--name", name,
              "--device", "/dev/fuse", "--cap-add", "SYS_ADMIN",
              "--hostname", "weftslurm", "-p", "127.0.0.1::22",
              "weft-test-slurm")
    assert run.returncode == 0, run.stderr
    port = _sh("docker", "port", name, "22").stdout.strip().rsplit(":", 1)[-1]
    key = str(keydir / "id_ed25519")
    ssh_opts = ["-i", key, "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "IdentitiesOnly=yes"]
    for _ in range(120):
        ok = _sh("ssh", *ssh_opts, "-o", "BatchMode=yes", "-p", port,
                 "physicist@127.0.0.1", "sinfo -h -o %a 2>/dev/null | head -1")
        if ok.returncode == 0 and "up" in ok.stdout.lower():
            break
        time.sleep(0.5)
    else:
        _sh("docker", "rm", "-f", name)
        pytest.skip("slurm fixture never became ready")

    def ssh(cmd: str):
        return _sh("ssh", *ssh_opts, "-p", port, "physicist@127.0.0.1", cmd)

    ssh(f"mkdir -p {DATA} && head -c 1000000 /dev/urandom > {DATA}/big.bin "
        f"&& echo v1 > {DATA}/meta.txt")
    yield {"host": "127.0.0.1", "port": int(port), "user": "physicist",
           "ssh_opts": ssh_opts, "root": "/home/physicist/.weft",
           "modules_init": "export MODULEPATH=/opt/site-modules",
           "ssh": ssh}
    _sh("docker", "rm", "-f", name)


@pytest.fixture(scope="module")
def comp(cluster):
    from core.compute import adapter as ad
    st = ad.configure()
    assert st["ok"], st["detail"]
    c = ad.get_compute()
    r = c.sync_call("register_site", "hpc", "slurm",
                    {"root": cluster["root"], "host": cluster["host"],
                     "port": cluster["port"], "user": cluster["user"],
                     "ssh_opts": cluster["ssh_opts"],
                     "modules_init": cluster["modules_init"]})
    assert r.get("site") == "hpc", r
    yield c
    from core.compute import adapter as ad2
    ad2.shutdown()


def _wait(comp, jid, n=360):
    for _ in range(n):
        s = comp.sync_call("task_status", jid)[0]
        if s["state"] in ("DONE", "FAILED", "CANCELLED"):
            return s
        time.sleep(0.5)
    raise AssertionError("task never finished")


def test_durable_home_on_cluster_zero_copy(comp):
    from core.data import datasets as ds
    fp = ds.fingerprint_site_path(DATA, "hpc")
    assert fp["exists"] and fp["n_files"] == 2
    assert fp["total_bytes"] == 1_000_003
    meta = ds.register_source(DATA, site="hpc")
    assert meta["origin_class"] == "path" and meta["ref"] is None
    assert meta["home"] == {"site": "hpc", "path": DATA}
    # descriptor is all the controller ever holds
    assert meta["descriptor"]["total_bytes"] == 1_000_003
    globals()["_META"] = meta


def test_slurm_task_reads_through_symlink(comp):
    from core.data import datasets as ds
    meta = globals()["_META"]
    ident = ds.ensure_ref(meta)
    assert ident["state"] == "ok" and ident["ref"].startswith("dref:")
    meta["ref"] = ident["ref"]
    t = comp.sync_call("task_submit",
                       {"command": "wc -c atlas/big.bin && ls -la atlas",
                        "site": "hpc", "label": "ds-read",
                        "inputs": [{"ref": meta["ref"], "mount_as": "atlas"}]})
    s = _wait(comp, t["job_id"])
    assert s["state"] == "DONE", s
    res = comp.sync_call("task_result", t["job_id"])
    assert "1000000" in res["logs"]["tail"]
    assert "->" in res["logs"]["tail"]           # atlas IS a symlink


def test_drift_fenced_presubmit_and_translated_at_staging(comp, cluster):
    from core.data import datasets as ds
    meta = globals()["_META"]
    cluster["ssh"](f"echo v2 >> {DATA}/meta.txt")   # mutate the home
    # (a) the pre-submit fence (the memo trap): ensure_ref reports drift
    out = ds.ensure_ref(meta)
    assert out["state"] == "drifted"
    # (b) a stale submit anyway → staging fence fails the job; the error
    #     translates to plain language with no weft jargon
    t = comp.sync_call("task_submit",
                       {"command": "true", "site": "hpc", "label": "ds-stale",
                        "inputs": [{"ref": meta["ref"], "mount_as": "atlas"}]})
    s = _wait(comp, t["job_id"])
    assert s["state"] == "FAILED"
    friendly = ds.explain_data_error(s.get("error"))
    assert friendly and DATA in friendly and "Re-register" in friendly
    assert "dref:" not in friendly
