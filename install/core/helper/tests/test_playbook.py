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


def test_install_yml_is_core_only():
    """The installer builds only platform CORE (misc/modules.md). The R toolchain,
    lstar bridge, and pagoda3 viewer dist are MODULES the backend reconciler owns —
    none appear as install steps. update.yml still refreshes an installed pagoda3."""
    from aba_installer.playbook import load_playbook
    p = Path(__file__).resolve().parents[1] / "src/aba_installer/install.yml"
    ids = [s.id for s in load_playbook(p).steps]
    for gone in ("create-r-tools-env", "install-lstar-r", "fetch-pagoda3-dist",
                 "complete-base-env", "complete-r-env"):
        assert gone not in ids, f"{gone} should not be an install step (module/reconciler owns it)"
    assert "pagoda3-viewer-0.2.1.zip" in _fetch_pagoda3_cmd("update.yml")   # update still refreshes


def _step_cmds(playbook: str, step_id: str) -> str:
    p = Path(__file__).resolve().parents[1] / "src/aba_installer" / playbook
    step = next(s for s in load_playbook(p).steps if s.id == step_id)
    return "\n".join(step.commands)


def test_env_build_isolates_from_user_site():
    """The conda env build (and its pip: section) MUST run with PYTHONNOUSERSITE=1
    so a pip package already in the user's ~/.local can't shadow the env — else
    micromamba's pip step skips it ("already satisfied"), env/bin/uvicorn is never
    created, start-backend fails, and the installer loops rebuilding (regression
    2026-07-10). Guard both the install (create) and update (env update) paths."""
    create = _step_cmds("install.yml", "create-env")
    assert "PYTHONNOUSERSITE=1" in create and 'micromamba" create' in create
    refresh = _step_cmds("update.yml", "refresh-env")
    assert "PYTHONNOUSERSITE=1" in refresh and 'micromamba" env update' in refresh


def test_refresh_env_makes_site_packages_writable():
    """micromamba links package dirs READ-ONLY, so a pip pin BUMP on `aba update`
    fails ("Lacking write permission") — pip can't replace the old package dir.
    refresh-env must chmod u+w site-packages BEFORE the env update that runs pip
    (regression 2026-07-11: lstar-sc 0.2.0→0.2.1 upgrade failed on a live box)."""
    refresh = _step_cmds("update.yml", "refresh-env")
    assert "chmod -R u+w" in refresh and "site-packages" in refresh
    assert refresh.index("chmod -R u+w") < refresh.index('micromamba" env update')


# ── lazy/staged env init (ABA_ENV_PREWARM) ──────────────────────────────────
def test_staged_create_env_picks_boot_and_marks_stage():
    create = _step_cmds("install.yml", "create-env")
    assert "environment-boot.yml" in create and "ABA_ENV_PREWARM" in create
    assert ".aba-base-stage" in create
    # eager default = the full manifest
    assert 'MANIFEST="$ABA_HOME/environment.yml"' in create


def test_r_and_viewer_build_lives_in_module_scripts():
    """R + pagoda3 build is owned by the module scripts now (install-r-bio.sh even runs
    the lstar bridge internally); the installer no longer has those steps."""
    scripts = Path(__file__).resolve().parents[1].parent / "modules"
    assert "install-lstar-r.sh" in (scripts / "install-r-bio.sh").read_text()
    assert "index.html" in (scripts / "install-viewer-pagoda3.sh").read_text()


def test_post_start_completion_moved_to_backend_modules():
    """Migration (misc/modules.md): the playbook's post-start `complete-base-env` /
    `complete-r-env` steps are GONE — the backend module reconciler owns post-install.
    start-backend is now the LAST step; the shared module scripts carry the logic."""
    from aba_installer.playbook import load_playbook
    root = Path(__file__).resolve().parents[1]
    pb = load_playbook(root / "src/aba_installer/install.yml")
    ids = [s.id for s in pb.steps]
    assert "complete-base-env" not in ids and "complete-r-env" not in ids
    assert ids[-1] == "start-backend"
    # The install logic lives in standalone, idempotent module scripts now.
    scripts = root.parent / "modules"
    py = (scripts / "install-python-bio.sh").read_text()
    assert "env update" in py and "completing" in py and "ready" in py
    assert "chmod -R u+w" in py and "PYTHONNOUSERSITE=1" in py
    r = (scripts / "install-r-bio.sh").read_text()
    assert "r-environment.yml" in r and "install-lstar-r.sh" in r
    pg = (scripts / "install-viewer-pagoda3.sh").read_text()
    assert "pagoda3" in pg and "index.html" in pg


def test_eager_builds_full_base_before_start():
    """Eager still builds the full base (create-env) before start-backend; R/pagoda3
    now come from the backend reconciler post-start (modules seeded 'on'), not pre-start
    install steps."""
    from aba_installer.playbook import load_playbook
    pb = load_playbook(Path(__file__).resolve().parents[1] / "src/aba_installer/install.yml")
    ids = [s.id for s in pb.steps]
    assert ids.index("create-env") < ids.index("start-backend")


def test_refresh_env_stamps_stage_ready():
    refresh = _step_cmds("update.yml", "refresh-env")
    assert ".aba-base-stage" in refresh and "printf ready" in refresh


def test_load_config_env_reaches_playbook_env(tmp_path, monkeypatch):
    """Deploy knobs written to config.env (ABA_ENV_PREWARM, …) must reach the
    playbook env even when the helper runs via a LaunchAgent (no shell exports) —
    the mac staged bug. A live export still wins over config.env."""
    import os
    from aba_installer import control
    (tmp_path / "config.env").write_text(
        '# c\nABA_ENV_PREWARM=staged\nexport ABA_ACCELERATOR=cpu\nABA_RUNTIME_DIR="/s/x"\nJUNK\n')
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("ABA_ACCELERATOR", "cuda")     # a live value must WIN
    monkeypatch.delenv("ABA_ENV_PREWARM", raising=False)
    control.load_config_env()
    assert os.environ["ABA_ENV_PREWARM"] == "staged"  # reached the env (LaunchAgent-safe)
    assert os.environ["ABA_RUNTIME_DIR"] == "/s/x"    # quotes stripped
    assert os.environ["ABA_ACCELERATOR"] == "cuda"    # live export not clobbered


def test_bg_skip_conditional_on_prewarm(monkeypatch):
    """Service bg-install: eager skips start-backend (the service starts it after
    auth); staged runs it in the bg worker so the server comes up credential-less
    right after the boot env. Absent ⇒ eager (current behaviour)."""
    from aba_installer import control
    monkeypatch.setenv("ABA_ENV_PREWARM", "eager")
    assert control._bg_skip() == {"start-backend"}
    monkeypatch.setenv("ABA_ENV_PREWARM", "staged")
    assert control._bg_skip() == set()
    monkeypatch.delenv("ABA_ENV_PREWARM", raising=False)
    assert control._bg_skip() == {"start-backend"}


def test_fetch_pagoda3_dist_is_version_aware():
    # A pinned URL + a marker so a version bump re-fetches on update (not skipped
    # on "index.html present"), with an atomic swap that keeps the old dist on
    # failure. If the pin bumps, update BOTH the URL and this assertion together.
    cmd = _fetch_pagoda3_cmd("update.yml")
    assert "pagoda3-viewer-0.2.1.zip" in cmd and "download/v0.2.1/" in cmd
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
