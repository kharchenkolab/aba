"""Activating one env must not hide the tooling needed to mount the next.

Field failure: a viewer converter ran inside the project's python env and then
had to reach a SECOND env (an R interpreter) to read its input. Both envs are
squashfs images mounted on demand. The mount failed and the substrate reported,
accurately, "squashfs env not mounted (no fuse/squashfuse here?)" — because a
successful activation REPLACES PATH with the activated env's own bin, and the
mount helper lives in the controller's bin, which is no longer on it.

Measured: before activation `command -v squashfuse` → the controller bin; after
a successful activation → not found. The substrate is blameless; the argv we
handed it could not have worked.

So every argv that (a) activates an env and (b) may have to mount another one
keeps the controller's bin reachable. `ns_wrap` is exactly the flag that says
"mounts are involved here", which makes it the right condition.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio

_CTRL_BIN = os.path.dirname(sys.executable)

_MOUNTED = {"source": "base", "env_id": "env:v1:abc", "prefix": "/mnt/x",
            "activation": ". /packs/x/activate.sh", "ns_wrap": True,
            "direct_exec": False}
_PLAIN = {"source": "session", "env_id": None, "prefix": "/plain/pfx",
          "activation": 'eval "$(pixi shell-hook)"', "ns_wrap": False,
          "direct_exec": True}


def _script(argv):
    """The shell text an argv actually runs (['bash','-c',SCRIPT])."""
    return argv[-1] if argv[:2] == ["bash", "-c"] else " ".join(argv)


def test_ns_wrapped_argv_keeps_the_mount_tooling_reachable():
    """THE field bug: inside the activated env, a nested mount must still find
    its helper."""
    from core.compute.project_env import argv_for_runtime
    script = _script(argv_for_runtime(_MOUNTED, "python", ["-c", "pass"]))
    assert _CTRL_BIN in script, (
        f"the ns-wrapped argv does not keep {_CTRL_BIN} on PATH — a second env "
        f"cannot be mounted from inside the first, and the substrate can only "
        f"report the helper missing:\n{script}")
    # …and BEFORE the activation: the mount happens DURING activation, so a
    # repair applied afterwards arrives too late to help the thing that needs it
    assert script.index(_CTRL_BIN) < script.index(_MOUNTED["activation"]), (
        "PATH is repaired after the activation that performs the mount — the "
        "helper has to be findable while the mount is happening")


def test_direct_exec_argv_is_untouched():
    """WIDE: a plain-prefix runtime execs the interpreter directly. No
    activation, no mount, nothing to repair — and no PATH surgery, which would
    only widen what that process can see."""
    from core.compute.project_env import argv_for_runtime
    argv = argv_for_runtime(_PLAIN, "python", ["-c", "pass"])
    assert argv[0] == str(Path("/plain/pfx") / "bin" / "python")
    assert _CTRL_BIN not in " ".join(argv)


def test_non_wrapped_activation_is_untouched():
    """DEGENERATE: activation without ns_wrap means no mount namespace and so
    no on-demand mount — leave it alone."""
    from core.compute.project_env import argv_for_runtime
    rt = {**_PLAIN, "direct_exec": False, "prefix": None}
    script = _script(argv_for_runtime(rt, "python", ["-c", "pass"]))
    assert "unshare" not in script
    assert _CTRL_BIN not in script


def test_rscript_shim_keeps_the_mount_tooling_reachable(tmp_path, monkeypatch):
    """The shim is the exact artifact that failed in the field: lstar execs it
    as a plain interpreter path, from inside the python env's namespace."""
    from content.bio.viewers.launchers import pagoda3
    from core.compute import project_env

    monkeypatch.setattr(project_env, "runtime", lambda pid, lang: dict(_MOUNTED))
    monkeypatch.setattr(pagoda3, "project_work_dir", lambda pid: str(tmp_path),
                        raising=False)
    import core.config as _cfg
    monkeypatch.setattr(_cfg, "project_work_dir", lambda pid: str(tmp_path),
                        raising=False)

    p = pagoda3._rscript_shim("prj_x")
    assert p, "no shim was built for a mount-scoped R runtime"
    body = Path(p).read_text()
    assert "unshare -rm" in body, "a mount-scoped runtime needs its namespace"
    assert _CTRL_BIN in body, (
        f"the shim drops {_CTRL_BIN}; mounting the R env from inside the "
        f"python env's namespace then fails with 'no fuse/squashfuse '"
        f"here':\n{body}")
    assert body.index(". /packs/x/activate.sh") > body.index(_CTRL_BIN), (
        "PATH must be repaired BEFORE the activation that needs the helper — "
        "the mount happens during activation, not after it")
