"""H2 — Playbook parser + step executor tests."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aba_installer.playbook import (
    Step, Playbook, CommandResult, StepResult,
    load_playbook, Executor,
)


# ─── parser ────────────────────────────────────────────────────────────────
def test_step_from_dict_minimal():
    s = Step.from_dict({"id": "x", "commands": ["echo hi"]})
    assert s.id == "x"
    assert s.title == "x"   # defaults to id
    assert s.commands == ["echo hi"]
    assert s.timeout_seconds == 300


def test_step_from_dict_full():
    s = Step.from_dict({
        "id": "x", "title": "X", "why": "because\n", "commands": ["a", "b"],
        "timeout_seconds": 42,
    })
    assert s.title == "X"
    assert s.why == "because"
    assert s.timeout_seconds == 42


def test_step_rejects_non_list_commands():
    with pytest.raises(ValueError):
        Step.from_dict({"id": "x", "commands": "echo hi"})


def test_load_playbook_install_yml():
    """The shipped install.yml must parse cleanly."""
    pb_path = Path(__file__).resolve().parents[1] / "src/aba_installer/install.yml"
    pb = load_playbook(pb_path)
    assert len(pb.steps) >= 5
    ids = [s.id for s in pb.steps]
    assert "preflight" in ids
    assert "install-micromamba" in ids
    assert "create-env" in ids
    # No duplicates
    assert len(set(ids)) == len(ids)


def test_load_playbook_update_yml():
    """The update playbook must parse cleanly."""
    up_path = Path(__file__).resolve().parents[1] / "src/aba_installer/update.yml"
    up = load_playbook(up_path)
    ids = [s.id for s in up.steps]
    assert "pull-aba" in ids
    assert "pull-recipes" in ids
    assert "refresh-env" in ids


def test_load_playbook_rejects_bad_root(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("- not a mapping\n")
    with pytest.raises(ValueError):
        load_playbook(bad)


def _fetch_pagoda3_cmd(playbook: str) -> str:
    p = Path(__file__).resolve().parents[1] / "src/aba_installer" / playbook
    step = next(s for s in load_playbook(p).steps if s.id == "fetch-pagoda3-dist")
    return step.commands[0]


def test_fetch_pagoda3_dist_identical_across_playbooks():
    # The engine has no include/compose, so this step is duplicated — it drifted
    # once (install-only → dark viewer on update). Guard byte-identity.
    assert _fetch_pagoda3_cmd("install.yml") == _fetch_pagoda3_cmd("update.yml")


def test_fetch_pagoda3_dist_is_version_aware():
    # A pinned URL + a marker so a version bump re-fetches on update (not skipped
    # on "index.html present"), with an atomic swap that keeps the old dist on
    # failure. If the pin bumps, update BOTH the URL and this assertion together.
    cmd = _fetch_pagoda3_cmd("update.yml")
    assert "pagoda3-viewer-0.2.0.zip" in cmd and "download/v0.2.0/" in cmd
    assert ".aba-dist-url" in cmd                 # version marker gates the skip
    assert '"$(cat "$MARK" 2>/dev/null)" = "$URL"' in cmd
    assert "0.1." not in cmd                       # no stale 0.1.x pin left behind


def test_install_yml_has_import_recipes_step():
    pb_path = Path(__file__).resolve().parents[1] / "src/aba_installer/install.yml"
    ids = [s.id for s in load_playbook(pb_path).steps]
    assert "import-recipes" in ids
    # must run AFTER the clone so the pack is on disk
    assert ids.index("import-recipes") > ids.index("clone-repos")


def test_import_recipes_maps_pack_into_installation_scope(tmp_path, monkeypatch):
    """import-recipes copies pack recipes/<domain>/ → installation
    skills/recipes/<domain>/ and catalog/*.yaml → catalog/ — the wiring that
    makes the recipe library actually surface to the agent."""
    # env_vars are rendered with os.path.expandvars against the real os.environ,
    # so redirect HOME + ABA_HOME there (NOT via base_env) to stay hermetic.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ABA_HOME", str(tmp_path / ".aba"))
    pb_path = Path(__file__).resolve().parents[1] / "src/aba_installer/install.yml"
    pb = load_playbook(pb_path)
    step = next(s for s in pb.steps if s.id == "import-recipes")

    pack = tmp_path / ".aba" / "repo" / "aba-recipe-pack"   # = $REPO_DIR/aba-recipe-pack
    (pack / "recipes" / "genomics").mkdir(parents=True)
    (pack / "recipes" / "genomics" / "scrna.md").write_text("# recipe\n")
    (pack / "recipes" / "biomni-derived" / "database").mkdir(parents=True)
    (pack / "recipes" / "biomni-derived" / "database" / "fetch.md").write_text("# b\n")
    (pack / "catalog").mkdir(parents=True)
    (pack / "catalog" / "python_bio.yaml").write_text("capabilities: []\n")
    # knowhow/ (incl. refsources/ — the fetch_reference provider catalog the
    # bundle loader composes) must also map into the installation scope.
    (pack / "knowhow" / "refsources").mkdir(parents=True)
    (pack / "knowhow" / "refsources" / "ensembl.yaml").write_text("provider: ensembl\nkind: manifest\n")
    (pack / "knowhow" / "scrna-analysis.md").write_text("# knowhow\n")

    res = Executor(pb).run_step(step)
    assert res.ok, res.error

    inst = tmp_path / ".aba" / "installation"
    assert (inst / "skills" / "recipes" / "genomics" / "scrna.md").exists()
    assert (inst / "skills" / "recipes" / "biomni-derived" / "database" / "fetch.md").exists()
    assert (inst / "catalog" / "python_bio.yaml").exists()
    # refsources provider catalog + cross-linked knowhow docs mapped through
    assert (inst / "knowhow" / "refsources" / "ensembl.yaml").exists()
    assert (inst / "knowhow" / "scrna-analysis.md").exists()


def test_step_lookup(tmp_path):
    p = Playbook(steps=[
        Step(id="a", title="A", why="", commands=["true"]),
        Step(id="b", title="B", why="", commands=["true"]),
    ])
    assert p.step("a").title == "A"
    assert p.step("missing") is None


# ─── executor — basic ──────────────────────────────────────────────────────
def _pb(*step_specs) -> Playbook:
    return Playbook(steps=[Step(id=sid, title=sid, why="", commands=cmds)
                           for sid, cmds in step_specs])


def test_executor_runs_simple_command():
    pb = _pb(("hello", ["echo hello-world"]))
    events = []
    ex = Executor(pb, on_event=lambda n, p: events.append((n, p)))
    results = ex.run_all()
    assert len(results) == 1
    r = results[0]
    assert r.ok
    assert r.commands[0].stdout.strip() == "hello-world"
    assert r.commands[0].exit_code == 0
    # Event stream — output is streamed line-by-line as command_output between
    # command_start and command_end.
    names = [n for n, _ in events]
    assert names == ["step_start", "command_start", "command_output",
                     "command_end", "step_end"]
    out_line = next(p["line"] for n, p in events if n == "command_output")
    assert out_line == "hello-world"


def test_executor_stops_on_command_failure():
    pb = _pb(
        ("ok",   ["echo a"]),
        ("fail", ["false"]),
        ("never", ["echo never-runs"]),
    )
    results = Executor(pb).run_all()
    assert [r.step_id for r in results] == ["ok", "fail"]
    assert results[0].ok
    assert not results[1].ok
    assert "command failed" in results[1].error


def test_executor_handles_timeout():
    pb = Playbook(steps=[
        Step(id="slow", title="slow", why="", commands=["sleep 2"], timeout_seconds=1)
    ])
    results = Executor(pb).run_all()
    assert not results[0].ok
    assert results[0].commands[0].timed_out is True


def test_executor_only_filter():
    pb = _pb(
        ("a", ["echo a"]),
        ("b", ["echo b"]),
        ("c", ["echo c"]),
    )
    results = Executor(pb).run_all(only={"a", "c"})
    assert [r.step_id for r in results] == ["a", "c"]


def test_executor_substitutes_env_vars(tmp_path):
    pb = Playbook(
        steps=[Step(id="check", title="check", why="",
                    commands=['echo "$ABA_HOME"'])],
        env_vars={"ABA_HOME": str(tmp_path)},
    )
    results = Executor(pb).run_all()
    assert results[0].commands[0].stdout.strip() == str(tmp_path)


def test_executor_envvar_expansion_uses_caller_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TEST_VAR", "abc")
    pb = Playbook(
        steps=[Step(id="x", title="x", why="", commands=['echo "$EXPANDED"'])],
        env_vars={"EXPANDED": "$MY_TEST_VAR"},
    )
    results = Executor(pb).run_all()
    assert results[0].commands[0].stdout.strip() == "abc"


def test_command_result_ok_flags():
    ok = CommandResult(command="x", exit_code=0, stdout="", stderr="", duration_s=0.01)
    assert ok.ok
    fail = CommandResult(command="x", exit_code=1, stdout="", stderr="", duration_s=0.01)
    assert not fail.ok
    timeout = CommandResult(command="x", exit_code=0, stdout="", stderr="", duration_s=0.01, timed_out=True)
    assert not timeout.ok
