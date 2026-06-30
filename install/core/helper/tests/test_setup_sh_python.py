"""Regression tests for the Linux setup.sh BOOTSTRAP-PYTHON resolver.

setup.sh once trusted any `python3` for which `import ensurepip` worked — so a
RHEL7 python3 (3.6) sailed past pre-flight and then died deep in pip
("No matching distribution found for setuptools>=68"). The resolver must now:

  * reject an interpreter older than 3.9 (check the VERSION, not just ensurepip),
  * pick the newest suitable interpreter already on PATH,
  * fall back to environment modules (HPC) and then a micromamba-bootstrapped
    python, and otherwise fail with a clear, actionable message.

These run setup.sh with ABA_RESOLVE_PYTHON_ONLY=1 — a test seam that resolves
PYBOOT and exits *before* the (45-min) install — under a controlled PATH of stub
interpreters, so the assertions are hermetic: no real python / modules / network.
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SETUP_SH = REPO_ROOT / "install/linux/setup.sh"

# A python3 stub whose version comes from its own name: python3.11 -> 3.11,
# python3.9 -> 3.9; a bare `python3` reports $STUB_PY3VER (default 3.6). It only
# answers the resolver's two probes — py_usable (imports ensurepip, exits 0 iff
# >=3.9) and py_ver (prints "maj.min"); the seam stops before any `-m venv`.
PY_STUB = r"""#!/usr/bin/env bash
self="$(basename "$0")"
case "$self" in
  python3.[0-9]|python3.[0-9][0-9]) ver="${self#python}";;
  *) ver="${STUB_PY3VER:-3.6}";;
esac
maj="${ver%%.*}"; min="${ver#*.}"
case "$*" in
  *ensurepip*) { [ "$maj" -eq 3 ] && [ "$min" -ge 9 ]; } && exit 0 || exit 1 ;;
  *print*)     echo "$maj.$min"; exit 0 ;;
  *)           exit 0 ;;
esac
"""

GIT_STUB = "#!/usr/bin/env bash\nexit 0\n"
# curl FAILS so the micromamba bootstrap cannot reach the network in tests.
CURL_FAIL = "#!/usr/bin/env bash\nexit 1\n"
# A `module` on PATH short-circuits the /etc/profile.d sourcing (keeping the test
# hermetic on a real HPC); this one advertises no python module.
MODULE_EMPTY = "#!/usr/bin/env bash\nexit 0\n"


def _mkbin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()

    def w(name, body):
        p = b / name
        p.write_text(body)
        p.chmod(0o755)
        return p

    w("git", GIT_STUB)
    return b, w


def _run(tmp_path, binr, **env):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    base = {
        "PATH": f"{binr}:/usr/bin:/bin",
        "HOME": str(home),
        "ABA_RESOLVE_PYTHON_ONLY": "1",
    }
    base.update(env)
    return subprocess.run(
        ["bash", str(SETUP_SH)], env=base,
        capture_output=True, text=True, timeout=120,
    )


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


def test_picks_newest_usable_python_on_path(tmp_path):
    """3.6 on PATH is rejected; a newer python3.11 alongside it is chosen."""
    binr, w = _mkbin(tmp_path)
    w("python3", PY_STUB)        # reports 3.6 -> unusable
    w("curl", CURL_FAIL)
    w("python3.11", PY_STUB)     # reports 3.11 -> usable
    r = _run(tmp_path, binr)
    assert r.returncode == 0, _out(r)
    assert "RESOLVED_PYBOOT=" in _out(r), _out(r)
    line = next(l for l in _out(r).splitlines() if l.startswith("RESOLVED_PYBOOT="))
    assert line.endswith("python3.11"), line          # not the 3.6 `python3`
    assert "(3.11)" in _out(r), _out(r)


def test_old_python_no_alternatives_fails_clearly(tmp_path):
    """Only a 3.6 on PATH, no module python, no network -> clean version error,
    NOT a raw pip/setuptools failure."""
    binr, w = _mkbin(tmp_path)
    w("python3", PY_STUB)        # 3.6
    w("curl", CURL_FAIL)         # blocks micromamba bootstrap
    w("module", MODULE_EMPTY)    # no python module available
    r = _run(tmp_path, binr)
    assert r.returncode != 0, "an unusable python with no fallback must abort"
    out = _out(r).lower()
    assert "no usable python" in out, _out(r)
    assert "setuptools" not in out, "must fail at the version gate, not deep in pip"


def test_explicit_aba_python_too_old_is_rejected(tmp_path):
    """ABA_PYTHON pointing at a 3.6 is validated and refused (not trusted blindly)."""
    binr, w = _mkbin(tmp_path)
    old = w("python3", PY_STUB)  # 3.6
    w("curl", CURL_FAIL)
    r = _run(tmp_path, binr, ABA_PYTHON=str(old))
    assert r.returncode != 0, _out(r)
    out = _out(r)
    assert "ABA_PYTHON" in out and "unusable" in out, out
    assert "3.6" in out, out


def test_resolver_keeps_module_and_micromamba_fallbacks(tmp_path):
    """Structural guard: the cluster module search and the micromamba bootstrap
    fallback must not be silently removed."""
    body = SETUP_SH.read_text()
    assert "try_module_python" in body
    assert "bootstrap_python_via_micromamba" in body
    assert "module -t avail python" in body          # the cluster python probe
    assert "micro.mamba.pm" in body                  # the bootstrap download


def test_resolver_prefers_self_contained_over_module():
    """The persistent helper venv must outlive the install shell, so the SELF-CONTAINED
    micromamba bootstrap is preferred over a module python (whose libpython vanishes
    when the module unloads). Guard the call ORDER in the fallback chain."""
    body = SETUP_SH.read_text()
    mm = body.index('|| bootstrap_python_via_micromamba')   # the call site, not the def
    mod = body.index('|| try_module_python')                # the call site, not the def
    assert mm < mod, "micromamba bootstrap must be attempted before the module python"


def test_hands_env_spec_from_local_checkout_to_create_env():
    """create-env runs before clone-repos, so on a private/local install the
    playbook would 404 fetching environment.yml from GitHub. setup.sh installs
    from the checkout, so it must point the conda spec at the local file."""
    body = SETUP_SH.read_text()
    assert "ABA_ENV_YML_SRC" in body, "must hand create-env the local environment.yml"
    assert "install/core/environment.yml" in body


def test_colocates_package_cache_with_install():
    """The micromamba package cache must sit on the same filesystem as the env so
    packages are HARDLINKED, not copied. On a cluster $HOME (default cache) and the
    install dir are often different NFS mounts — a cross-mount install copies GBs of
    package data file-by-file. setup.sh must pin the cache under $ABA_HOME."""
    body = SETUP_SH.read_text()
    assert "CONDA_PKGS_DIRS" in body, "must pin the conda/mamba package cache"
    assert "MAMBA_ROOT_PREFIX" in body
    assert "$ABA_HOME/pkgs" in body, "cache must live under the install dir (same FS as the env)"
    assert "PIP_CACHE_DIR" in body, "pip's cache must be pinned under the install too ($HOME stays clean)"
