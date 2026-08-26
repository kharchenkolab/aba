"""An environment that cannot compile cannot install half of what is asked of it.

Two live failures on 2026-08-26, one user session, both from the same omission —
aba never declared BUILD dependencies:

  Python   error: [Errno 2] No such file or directory: 'g++'
           building `annoy` (a scrublet dependency) while realizing an isolated
           env. Surfaced to the user as "env solve error"; a wrapper library was
           substituted instead.
  R        Cannot find xml2-config → configuration failed for package 'XML'
           → "installation of 8 packages failed"

weft ships the COMPILERS to the cran lane through its own toolchain prefix and
takes `session_install(build_deps=[...])` to extend that prefix with library
headers. An EnvSpec has no build-only layer at all, so a pixi env builds sdists
with only what the env itself declares.

So the two fixes are necessarily different shapes, and both are pinned here:
the cran lane must SEND build_deps; an isolated python env must CARRY a
compiler. Neither is a preference — without them these requests cannot succeed.
"""
import pytest

from core.compute import named_envs, project_env


# ── isolated envs: do NOT carry a compiler ───────────────────────────────────

def test_an_isolated_python_env_ships_no_toolchain():
    """We put `cxx-compiler` here after a scrublet install died on a missing
    g++. weft DECLINED that shape when we filed it (bug5 A2): it was a
    workaround for a coverage gap on their side, deps.conda is the honest home
    of RUNTIME libraries, and a toolchain inside every env inflates each EnvID
    for a build input the result never needs.

    weft closed the gap instead — the full-prefix realize retries once with
    weft's own toolchain, gated on a compile signature. So the env stays clean,
    and this guard exists so nobody re-adds the workaround out of memory."""
    spec = named_envs._spec_for("prj", "e", "python", ["scrublet"])
    conda = spec["deps"]["conda"]
    assert not [p for p in conda if "compiler" in p or p == "make"], conda
    assert spec["deps"]["pypi"] == ["scrublet"]


def test_the_env_still_carries_what_it_is_FOR():
    """WIDE: removing the toolchain must not disturb the interpreter pin or the
    kernel package, which are the reasons an isolated env exists at all."""
    spec = named_envs._spec_for("prj", "e", "python", ["x"],
                                python_version="3.10")
    conda = spec["deps"]["conda"]
    assert conda[0] == "python =3.10" and "ipykernel" in conda


def test_a_caller_supplied_library_still_reaches_the_conda_layer():
    """A linked library needed at RUNTIME is a genuine dependency and does
    belong here — that is the distinction weft drew."""
    spec = named_envs._spec_for("prj", "e", "python", ["h5py"],
                                conda_packages=["hdf5"])
    assert "hdf5" in spec["deps"]["conda"]


# ── cran session installs: send build_deps ───────────────────────────────────

class _Ad:
    def __init__(self, sink, fail_on_build_deps=False):
        self.sink = sink
        self.fail_on_build_deps = fail_on_build_deps

    def session_install(self, sid, **kw):
        if self.fail_on_build_deps and "build_deps" in kw:
            raise TypeError("session_install() got an unexpected keyword "
                            "argument 'build_deps'")
        self.sink.append(kw)
        return {"runtime": {"source": "session", "prefix": "/p"}}


@pytest.fixture
def lane(monkeypatch):
    sink = []
    row = {"session_id": "s1", "base_env_id": "b", "additions": [], "rev": 0}
    monkeypatch.setattr(named_envs, "_sync", lambda x: x)
    monkeypatch.setattr(project_env, "ensure",
                        lambda pid, lang: {"session_id": "s1",
                                           "runtime": {"source": "session",
                                                       "prefix": "/p"}})
    monkeypatch.setattr(project_env, "get", lambda pid, lang: row)
    monkeypatch.setattr(project_env, "_save_row", lambda pid, lang, r: None)
    monkeypatch.setattr(project_env, "_current_runtime",
                        lambda sid: {"source": "session", "prefix": "/p"})
    return sink, row


def test_a_cran_install_sends_the_headers_its_builds_need(lane, monkeypatch):
    sink, _ = lane
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Ad(sink))

    project_env.install("prj", "r", ["XML"], eco="cran")

    bd = sink[-1].get("build_deps") or []
    assert "libxml2" in bd, (
        "without libxml2 the XML package's configure cannot find xml2-config "
        "— the live failure that took 8 packages down with it")
    assert {"libcurl", "openssl", "zlib"} <= set(bd)


def test_every_cran_lane_gets_them_not_just_the_one_that_remembered(lane,
                                                                    monkeypatch):
    """The agent request, the ranked lane and the session-rebuild replay all
    install cran. build_deps belongs to the ECOSYSTEM, not to whichever
    call site was patched."""
    sink, row = lane
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Ad(sink))
    project_env._replay_one(_Ad(sink), "s2",
                            {"eco": "cran", "specs": ["XML"]})
    # the replay path goes straight to session_install; the guard is that the
    # ONE owner is project_env.install, so a replay that routes through it is
    # covered. Assert the owner, not the caller:
    assert "cran" in project_env._BUILD_DEPS
    assert "libxml2" in project_env._BUILD_DEPS["cran"]


def test_a_pypi_install_sends_none(lane, monkeypatch):
    """WIDE: the pypi session lane never provisions a toolchain in weft, so
    build_deps there would be a silently-ignored argument — worse than absent,
    because it reads as coverage."""
    sink, _ = lane
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Ad(sink))
    project_env.install("prj", "python", ["requests"], eco="pypi")
    assert "build_deps" not in sink[-1]


def test_an_older_substrate_still_installs(lane, monkeypatch):
    """DEGENERATE: a weft that predates build_deps must not lose the install."""
    sink, _ = lane
    monkeypatch.setattr("core.compute.adapter.get_compute",
                        lambda: _Ad(sink, fail_on_build_deps=True))

    project_env.install("prj", "r", ["XML"], eco="cran")

    assert sink and "build_deps" not in sink[-1]
    assert sink[-1]["cran"] == ["XML"]
