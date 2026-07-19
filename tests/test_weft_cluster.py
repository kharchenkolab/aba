"""W3.1 (weft rewrite): the aba compute lane against a REAL (mock) Slurm
cluster — weft's own dockerized single-node fixture, driven end-to-end through
ABA's adapter, sites-config, and seeding modules. Domain-generic throughout.

OPT-IN (slow, needs docker group):
    sg docker -c "ABA_WEFT_CLUSTER=1 ABA_PIXI_BIN=... .venv/bin/python -m pytest tests/test_weft_cluster.py -q"

Covers:
  * weft-sites.yaml (kind: slurm) → registered cluster site at configure()
  * bare task round trip on the cluster (+ placement provenance site=hpc)
  * admin publish (seeding.publish_base_packs) → versioned catalog on the
    cluster → a SECOND workspace adopts by name (no solve) → runs on the
    adopted RO base → extends it (project delta overlay)
  * base_env adoption: the consumer's default lane resolves the pack via the
    published catalog (ABA_WEFT_PUBLISH_TREE), not a private solve
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

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_cluster_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
# The W3.3 lane test runs aba's node entry INSIDE the container as its own
# user over the shared mount — host-created dirs must be node-writable.
os.umask(0)
os.chmod(_tmp, 0o777)

pytestmark = [
    pytest.mark.platform,
    pytest.mark.skipif(not os.environ.get("ABA_WEFT_CLUSTER"),
                       reason="set ABA_WEFT_CLUSTER=1 (and run under sg docker) "
                              "for the mock-cluster round trip"),
]

TREE = "/srv/aba-envs"          # the published catalog tree ON the cluster


def _sh(*args, timeout=600):
    return subprocess.run(list(args), capture_output=True, text=True,
                          timeout=timeout)


def _weft_repo() -> Path:
    import weft
    return Path(weft.__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cluster():
    """weft's dockerized single-node Slurm cluster (their fixture, our launch)."""
    if _sh("docker", "info").returncode != 0:
        pytest.skip("docker not available (run under `sg docker`)")
    fixtures = _weft_repo() / "tests" / "fixtures" / "slurm"
    if not fixtures.exists():
        pytest.skip("weft repo checkout with tests/fixtures/slurm not found")
    keydir = Path(tempfile.mkdtemp(prefix="aba_slurmkeys_"))
    build = _sh("sh", str(fixtures / "build.sh"), str(keydir))
    if build.returncode != 0:
        pytest.skip(f"cannot build slurm fixture: {build.stderr[-300:]}")
    name = f"aba-weft-slurm-{uuid.uuid4().hex[:8]}"
    # --privileged (not just --device /dev/fuse + SYS_ADMIN, weft's polite
    # subset): this host's older docker/kernel refuses unprivileged fuse
    # mounts and `unshare -rm` inside containers, which breaks the squashfs
    # publish spot-check. We're validating the aba↔weft flow, not container
    # security.
    # Shared-FS mock (the real deployments' contract — server + nodes see the
    # same paths): the aba checkout+venv read-only, the test runtime rw.
    run = _sh("docker", "run", "-d", "--rm", "--name", name, "--privileged",
              "-v", "/home/pkharchenko/aba:/home/pkharchenko/aba:ro",
              "-v", f"{_tmp}:{_tmp}",
              "--hostname", "weftslurm", "-p", "127.0.0.1::22", "weft-test-slurm")
    assert run.returncode == 0, run.stderr
    port = _sh("docker", "port", name, "22").stdout.strip().rsplit(":", 1)[-1]
    key = str(keydir / "id_ed25519")
    ssh_opts = ["-i", key, "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes"]
    ready = False
    for _ in range(120):
        ok = _sh("ssh", *ssh_opts, "-o", "BatchMode=yes", "-p", port,
                 "physicist@127.0.0.1", "sinfo -h -o %a 2>/dev/null | head -1")
        if ok.returncode == 0 and "up" in ok.stdout.lower():
            ready = True
            break
        time.sleep(0.5)
    if not ready:
        _sh("docker", "rm", "-f", name)
        pytest.skip("slurm fixture never became ready")
    yield {"container": name, "host": "127.0.0.1", "port": int(port),
           "user": "physicist", "ssh_opts": ssh_opts,
           "root": "/home/physicist/.weft"}
    _sh("docker", "rm", "-f", name)


@pytest.fixture(scope="module")
def hpc(cluster):
    """The cluster registered through ABA's deployment config (weft-sites.yaml)
    + a configured adapter — the exact path a real install takes."""
    from core.compute import adapter as ad
    home = Path(os.environ["ABA_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "weft-sites.yaml").write_text(json.dumps({"sites": [{
        "name": "hpc", "kind": "slurm",
        "config": {"host": cluster["host"], "port": cluster["port"],
                   "user": cluster["user"], "ssh_opts": cluster["ssh_opts"],
                   "root": cluster["root"]},
    }]}))
    ad.shutdown()
    ad._adapter = None
    st = ad.configure()
    assert st["ok"], st["detail"]
    w = ad.get_compute()
    w._weft.runner.poll_interval = 0.4
    yield w
    ad.shutdown()
    ad._status = {"ok": False, "severity": "info", "detail": "torn down by test"}


def _wait_done(w, job_id, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = w.sync_call("task_status", job_id)[0]["state"]
        if st in ("DONE", "FAILED", "CANCELLED"):
            return st
        time.sleep(1)
    return "TIMEOUT"


def test_sites_config_registers_cluster(hpc):
    names = {s["name"] for s in hpc.sync_call("sites_list")}
    assert {"local", "hpc"} <= names
    desc = hpc.sync_call("sites_describe", "hpc")
    assert desc, desc


def test_bare_task_round_trip_with_placement(hpc):
    r = hpc.sync_call("task_submit", {
        "command": "hostname; echo CLUSTER_OK", "site": "hpc",
        "label": "aba w3.1 smoke"})
    assert _wait_done(hpc, r["job_id"]) == "DONE"
    res = hpc.sync_call("task_result", r["job_id"])
    assert "CLUSTER_OK" in res["logs"]["tail"]
    prov = hpc.sync_call("provenance", r["job_id"])
    assert prov["placement"]["site"] == "hpc"
    assert prov["placement"]["node"]


@pytest.fixture(scope="module")
def published(hpc, cluster, monkeypatch_module=None):
    """Admin side: a tiny generic base pack published (versioned) into the
    cluster catalog via ABA's seeding module (bundle stubbed with the pack)."""
    from core.bundle.loader import EnvPack
    import core.bundle.active as active
    from core.compute import seeding
    pack = EnvPack("net-base", {
        "name": "net-base", "languages": ["python"], "role": "base",
        "spec": {"platforms": ["linux-64"],
                 "deps": {"conda": ["python =3.12", "ipykernel"]}},
    }, "system")
    orig = active.get_bundle
    active.get_bundle = lambda: type("B", (), {"env_packs": [pack]})()
    try:
        _sh("docker", "exec", cluster["container"], "sh", "-c",
            f"mkdir -p {TREE} && chown physicist {TREE}")
        rows = seeding.publish_base_packs(site="hpc", tree=TREE,
                                          version="2026.07-test")
        assert rows and rows[0].get("published"), rows
        yield {"pack": "net-base", "env_id": rows[0]["env_id"],
               "version": rows[0]["version"]}
    finally:
        active.get_bundle = orig


def test_publish_then_catalog_rows(hpc, published):
    cat = hpc.sync_call("env_published", "hpc", TREE)
    rows = cat.get("published") or cat.get("rows") or cat
    text = json.dumps(rows)
    assert "net-base" in text and published["version"] in text


def test_second_workspace_adopts_and_extends(cluster, published):
    """The consumer story: a FRESH workspace adopts by name (no solve), runs on
    the adopted RO base, then extends it with a project delta."""
    from core.compute.adapter import WeftAdapter, resolve_pixi
    consumer = WeftAdapter(Path(_tmp) / "consumer-ws", resolve_pixi())
    w = consumer
    w._weft.runner.poll_interval = 0.4
    w.sync_call("register_site", "hpc", "slurm", {
        "host": cluster["host"], "port": cluster["port"],
        "user": cluster["user"], "ssh_opts": cluster["ssh_opts"],
        "root": "/home/physicist/.weft-consumer",
        "pixi_source": resolve_pixi(),
        "ro_roots": [TREE]})
    adopted = w.sync_call("env_adopt", "hpc", TREE, "net-base")
    assert adopted["env_id"] == published["env_id"]      # same identity, no solve

    r = w.sync_call("task_submit", {
        "command": "python -c 'import sys, ipykernel; print(\"ADOPTED\", sys.version.split()[0])'",
        "env": adopted["env_id"], "site": "hpc", "label": "adopted run"})
    assert _wait_done(w, r["job_id"]) == "DONE"
    res = w.sync_call("task_result", r["job_id"])
    assert "ADOPTED 3.12" in res["logs"]["tail"]

    # project delta over the adopted RO base (frozen parent, O(delta) overlay)
    child = w.sync_call("env_ensure", {
        "name": "net-base-plus", "extends_env": adopted["env_id"],
        "platforms": ["linux-64"], "deps": {"pypi": ["sortedcontainers"]}})
    assert child.get("delta", {}).get("layerable") is True, child
    assert child["env_id"] != adopted["env_id"]
    r2 = w.sync_call("task_submit", {
        "command": "python -c 'import sortedcontainers, ipykernel; print(\"EXTENDED\", sortedcontainers.__version__)'",
        "env": child["env_id"], "site": "hpc", "label": "extended run"})
    assert _wait_done(w, r2["job_id"]) == "DONE"
    assert "EXTENDED" in w.sync_call("task_result", r2["job_id"])["logs"]["tail"]
    consumer.close()


def test_background_job_rides_the_weft_slurm_lane(hpc, monkeypatch):
    """W3.3: ABA_BATCH_SUBMITTER=slurm + a declared slurm-kind weft site →
    run_python(background=True)'s job becomes a weft task ON THE CLUSTER,
    running the same node entry over the shared FS; the result carries the
    weft compute block with cluster placement."""
    from core import projects
    from core.graph.jobs import get_job
    from core.jobs.submit import submit_python_job
    from core.jobs.submitter import get_submitter
    from core.jobs.weft_submitter import WeftSubmitter, weft_slurm_site
    monkeypatch.setenv("ABA_BATCH_SUBMITTER", "slurm")

    assert weft_slurm_site() == "hpc"
    sub = get_submitter(kind="run_python")
    assert type(sub).__name__ == "WeftSubmitter" and sub.site == "hpc"
    # nextflow heads stay on the legacy lane until W3.4
    assert type(get_submitter(kind="run_nextflow")).__name__ == "SlurmSubmitter"

    projects.init()
    pid = projects.create_project("w33")["id"]
    projects.set_current(pid)
    code = ("import platform; print('NODE', platform.node()); "
            "open('cluster_out.csv','w').write('a\\n1\\n')")
    job = submit_python_job(code, "w3.3 cluster bg", None, project_id=pid,
                            thread_id="t1", estimate={"cores": 1})
    row = get_job(job["id"], project_id=pid)
    params = row["params"] or {}
    assert params.get("submitter") == "weft" and params.get("weft_site") == "hpc", params
    assert params.get("weft_id", "").startswith("jb_")

    poller = WeftSubmitter(site="hpc")
    result = None
    deadline = time.time() + 600
    while time.time() < deadline:
        row = get_job(job["id"], project_id=pid)
        row["project_id"] = pid
        result = poller.poll(row)
        if result is not None:
            break
        time.sleep(2)
    assert result is not None, "cluster job did not terminate"
    assert result.get("returncode") == 0, result
    assert "NODE weftslurm" in (result.get("stdout") or "")
    assert any("cluster_out" in str(f) for f in
               (result.get("files") or []) + (result.get("tables") or [])), result
    comp = result.get("compute") or {}
    assert comp.get("substrate") == "weft"
    assert (comp.get("placement") or {}).get("site") == "hpc"
    assert (comp.get("placement") or {}).get("node") == "weftslurm"


def test_base_env_resolves_via_catalog(hpc, published, monkeypatch):
    """The default lane on a catalog-configured deployment ADOPTS the base pack
    (no private solve) — base_env.env_id == the published EnvID."""
    from core.bundle.loader import EnvPack
    import core.bundle.active as active
    from core.compute import base_env
    monkeypatch.setattr(active, "get_bundle", lambda: type(
        "B", (), {"env_packs": [EnvPack("net-base", {
            "name": "net-base", "languages": ["python"], "role": "base",
            "spec": {"platforms": ["linux-64"],
                     "deps": {"conda": ["python =3.12", "ipykernel"]}}},
            "system")]})())
    monkeypatch.setenv("ABA_WEFT_PUBLISH_TREE", TREE)
    monkeypatch.setenv("ABA_WEFT_PUBLISH_SITE", "hpc")
    base_env.reset_cache()
    assert base_env.env_id("python") == published["env_id"]
    base_env.reset_cache()
