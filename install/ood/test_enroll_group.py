"""enroll-group stamps the workspace + records enrollment + (optional) lab key,
and the result passes aba_preflight's enrollment gate. Refuses foreign folders."""
import importlib
import json
import os
import stat

import pytest
import yaml


def _site(g, skel):
    return f"""
site: {{name: VBC}}
scopes:
  group: {{enabled: true, root_path: "{g}/{{group}}/aba", auto_create_skeleton: false, skeleton_template: "{skel}"}}
  user:  {{state_dir: "{g}/{{group}}/aba/users/{{user}}"}}
credentials: {{order: [group_shared, user_form_paste], group_key_path: "{g}/{{group}}/aba/.credentials.json", on_missing: demo_mode}}
"""


def _mk_skeleton(tmp_path):
    skel = tmp_path / "skel"; (skel / "bundle").mkdir(parents=True)
    (skel / ".aba-workspace").write_text("# marker\n")
    (skel / "refs").mkdir(); (skel / "refs" / ".gitkeep").touch()
    return skel


def test_enroll_creates_workspace_records_and_passes_gate(tmp_path, monkeypatch):
    import enroll_group; importlib.reload(enroll_group)
    g = tmp_path / "groups"; skel = _mk_skeleton(tmp_path)
    site = tmp_path / "site.yaml"; site.write_text(_site(g, skel))
    enroll_group.main(["lab1", "--site", str(site), "--api-key", "sk-ant-api-TESTKEY", "--by", "tester"])

    aba = g / "lab1" / "aba"
    rec = (aba / ".aba-workspace").read_text()
    assert "enrolled_by: tester" in rec and "credential: api-key" in rec      # enrollment record
    cred = aba / ".credentials.json"
    assert json.loads(cred.read_text())["anthropic_api_key"] == "sk-ant-api-TESTKEY"
    assert stat.S_IMODE(os.stat(cred).st_mode) == 0o600                       # secret perms

    # the enrolled group now passes the preflight gate + resolves the lab key
    import aba_preflight; importlib.reload(aba_preflight)
    staged = tmp_path / "staged"; staged.mkdir()
    for k, v in {"ABA_SITE_CONFIG": str(site), "ABA_PF_GROUP": "lab1", "ABA_PF_USER": "alice",
                 "ABA_PF_HOME": str(tmp_path / "home"), "ABA_PF_STAGED": str(staged)}.items():
        monkeypatch.setenv(k, v)
    aba_preflight.main()                                                       # must not raise (enrolled)
    st = yaml.safe_load((staged / "status.yaml").read_text())
    assert st["ready"] is True and st["scopes"]["group"]["state"] == "ok"
    assert st["credentials"]["resolved"] is True


def test_enroll_idempotent_and_refuses_foreign(tmp_path):
    import enroll_group; importlib.reload(enroll_group)
    g = tmp_path / "groups"; skel = _mk_skeleton(tmp_path)
    site = tmp_path / "site.yaml"; site.write_text(_site(g, skel))
    enroll_group.main(["lab1", "--site", str(site)])
    enroll_group.main(["lab1", "--site", str(site)])                          # idempotent
    assert (g / "lab1" / "aba" / ".aba-workspace").exists()

    (g / "lab2" / "aba").mkdir(parents=True); (g / "lab2" / "aba" / "x.txt").write_text("not ours")
    with pytest.raises(SystemExit):                                            # foreign → refuse
        enroll_group.main(["lab2", "--site", str(site)])
