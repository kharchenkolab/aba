"""A deployment must be able to declare its compute sites in site.yaml.

Found live 2026-08-25 on the OOD deployment. site.yaml said
`jobs: {submitter: slurm}` and the cluster had a slurm partition ready, but
`$ABA_HOME/weft-sites.yaml` — the ONLY place sites were read from — did not
exist and nothing in the OOD path ever writes it. So `weft_slurm_site()`
returned None and core/jobs/submitter._slurm_lane did this:

    print("[jobs] ABA_BATCH_SUBMITTER=slurm but no slurm-kind weft site
           declared (weft-sites.yaml) — running background jobs on the
           LOCAL weft lane")
    return _local_lane()

Every "background" job therefore ran INSIDE the user's OOD session container:
no scheduler, no allocation, competing with the interactive kernel — while
the user reasonably believed they were on Slurm. The only trace was one line
in the server log. Verified in the field session: sites registered = [local]
only, and a run_r job recorded submitter=weft, site=None.

`weft-sites.yaml` is per-ABA_HOME and written by the installer, so it can
never serve a shared deployment whose users each get a fresh ABA_HOME. The
deployment's own config file is the right home for a deployment-wide fact.
Precedence: an operator's weft-sites.yaml still wins per site name.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def _write(tmp_path, monkeypatch, site_yaml: str, sites_yaml: str | None):
    home = tmp_path / "home"; (home / "installation").mkdir(parents=True)
    sc = tmp_path / "site.yaml"; sc.write_text(site_yaml)
    monkeypatch.setenv("ABA_HOME", str(home))
    monkeypatch.setenv("ABA_SITE_CONFIG", str(sc))
    if sites_yaml is not None:
        (home / "weft-sites.yaml").write_text(sites_yaml)
    for m in ("core.config", "core.compute.sites_config"):
        sys.modules.pop(m, None)
    from core.compute import sites_config
    return sites_config


SITE_WITH_CLUSTER = """
jobs:
  submitter: slurm
compute:
  self_service: false
  sites:
    - name: cluster
      kind: slurm
      config:
        root: /scratch/u/weft-cluster
        policy:
          partitions_allowed: [c, g]
"""


def test_site_yaml_can_declare_a_compute_site(tmp_path, monkeypatch):
    """THE regression. With no weft-sites.yaml the deployment must still get
    its cluster site from site.yaml — otherwise a slurm deployment silently
    runs every background job in the session container."""
    sc = _write(tmp_path, monkeypatch, SITE_WITH_CLUSTER, None)
    names = [s.get("name") for s in sc.list_declared_sites()]
    assert "cluster" in names, (
        f"site.yaml declared a slurm site and it did not reach the site list "
        f"({names}) — the deployment degrades to the local lane")
    entry = next(s for s in sc.list_declared_sites() if s["name"] == "cluster")
    assert entry.get("kind") == "slurm"
    assert (entry.get("config") or {}).get("root")


def test_operator_weft_sites_still_wins_per_name(tmp_path, monkeypatch):
    """WIDE: site.yaml is the deployment default, not a lock. An operator's
    $ABA_HOME/weft-sites.yaml keeps overriding a site of the same name."""
    sc = _write(tmp_path, monkeypatch, SITE_WITH_CLUSTER, """
sites:
  - name: cluster
    kind: slurm
    config: {root: /operator/override}
""")
    entry = next(s for s in sc.list_declared_sites() if s["name"] == "cluster")
    assert (entry.get("config") or {}).get("root") == "/operator/override"


def test_no_sites_anywhere_is_still_empty(tmp_path, monkeypatch):
    """WIDE: a deployment declaring nothing must not gain phantom sites."""
    sc = _write(tmp_path, monkeypatch, "jobs:\n  submitter: local\n", None)
    assert sc.list_declared_sites() == []


SITE_PER_USER = """
jobs:
  submitter: slurm
compute:
  sites:
    - name: cluster
      kind: slurm
      config:
        root: /scratch/users/{user}/aba-runtime/weft-cluster
        policy:
          storage:
            scratch: /scratch/users/{user}
"""


def test_site_paths_expand_the_user_placeholder(tmp_path, monkeypatch):
    """A shared deployment declares ONE site for ALL users, so its paths must
    be per-user templates. Each user needs their own weft root — a literal
    path would have every user of the cluster sharing one workspace, which is
    both wrong and a permissions failure. {user}/{home} are the placeholders
    the bundle scopes already use; site configs must honour the same ones,
    including nested ones like policy.storage.scratch."""
    monkeypatch.setenv("USER", "alice")
    sc = _write(tmp_path, monkeypatch, SITE_PER_USER, None)
    entry = next(s for s in sc.list_declared_sites() if s["name"] == "cluster")
    cfg = entry["config"]
    assert "{user}" not in cfg["root"], f"unexpanded template: {cfg['root']}"
    assert cfg["root"] == "/scratch/users/alice/aba-runtime/weft-cluster"
    assert cfg["policy"]["storage"]["scratch"] == "/scratch/users/alice", \
        "nested config values must expand too"


def test_regtest_precondition_sees_site_yaml_sites(tmp_path, monkeypatch):
    """The regtest harness must agree with the backend about what counts.

    preconditions.declared_slurm_sites() reads ONLY $ABA_HOME/weft-sites.yaml
    and refuses `requires: slurm` scenarios when it finds nothing — correctly,
    because ABA_BATCH_SUBMITTER=slurm would otherwise degrade to the local lane
    and certify cluster coverage that never touched a cluster. That probe is
    why slurm scenarios were trustworthy on a personal install.

    Once a deployment can declare its site in site.yaml, a probe that only
    reads weft-sites.yaml would DECLINE every slurm scenario on exactly the
    deployment shape we most need to test. Two sources of truth for "is there
    a slurm site" is how the shapes diverged in the first place.
    """
    import sys as _sys
    _sys.path.insert(0, str(REPO / "regtest" / "harness"))
    home = tmp_path / "home"; home.mkdir(parents=True, exist_ok=True)
    sc = tmp_path / "site.yaml"; sc.write_text(SITE_WITH_CLUSTER)
    monkeypatch.setenv("ABA_SITE_CONFIG", str(sc))
    for m in [k for k in list(_sys.modules) if k == "preconditions"]:
        _sys.modules.pop(m, None)
    import preconditions
    got = preconditions.declared_slurm_sites(home)
    assert "cluster" in got, (
        f"site.yaml declares a slurm site; the regtest probe reports {got} and "
        f"would decline every requires:slurm scenario on this deployment shape")
