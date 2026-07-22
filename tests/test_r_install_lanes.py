"""Which lane an R install takes — the difference between working and refused
on an adopted base.

Live 2026-07-21: a GitHub R package was routed to the bespoke-installer lane,
which refuses on an adopted/unpacked base ("a bespoke installer needs a writable
clone of the base"). Every non-CRAN R package was therefore uninstallable on a
mount-adopted deployment. The substrate's cran lane speaks the whole spec
vocabulary — plain names, `name ==X.Y.Z`, `owner/repo@ref` — and composes the
session rlib delta-only over a read-only base, so a git source belongs THERE.

These are routing assertions: they pin which substrate verb is called with what,
because the observable difference (refused / full-realize / overlay) lives
entirely in that choice.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio


class _Recorder:
    """Stands in for project_env, recording lane + arguments."""
    def __init__(self, *, cran_ok=True):
        self.installs: list = []
        self.installers: list = []
        self._cran_ok = cran_ok

    def install(self, pid, language, specs, *, eco="pypi", **opts):
        self.installs.append({"lang": language, "specs": list(specs),
                              "eco": eco, "opts": opts})
        if eco == "conda":
            raise RuntimeError("no conda build")        # force the next lane
        if eco == "cran" and not self._cran_ok:
            raise RuntimeError("session.cold_base: substrate predates the vocabulary")
        return {"ok": True}

    def run_installer(self, pid, language, cmd, *, note="", writes_to=None):
        self.installers.append({"cmd": cmd, "note": note, "writes_to": writes_to})
        return {"ok": True}


def _run(monkeypatch, *, source, package, ref=None, cran_ok=True):
    from content.bio.tools import discovery
    rec = _Recorder(cran_ok=cran_ok)
    monkeypatch.setattr(discovery, "_r_version_in_session",
                        lambda *_a, **_k: "1.0" if False else None)
    fake_pe = types.SimpleNamespace(install=rec.install, run_installer=rec.run_installer)
    fake_cm = types.SimpleNamespace(project_env=fake_pe)
    monkeypatch.setitem(sys.modules, "core.compute", fake_cm)
    def _lane(pid, spec, **k):
        # honours _cran_lane's REAL contract — (landed, rendered_error, info).
        # The original stub returned a bare bool, which blew up the caller's
        # tuple unpack inside its try block: the flow silently took the error
        # exit and test_github_falls_back_* asserted on a fallback that never
        # ran. This file was not in the gated suite, so the contract change
        # that broke it went unnoticed.
        if cran_ok:
            rec.install(pid, "r", [spec], eco="cran")
            return True, None, {}
        return _false(rec, spec)

    monkeypatch.setattr(discovery, "_cran_lane", _lane)
    prov = {"source": source, "package": package}
    if ref:
        prov["ref"] = ref
    cap = {"name": package, "provisioning": {"r": prov}}
    try:
        discovery._ensure_r_via_session(cap, {}, None, package)
    except Exception:  # noqa: BLE001 — post-install verification is out of scope
        pass
    return rec


def _false(rec, spec):
    rec.installs.append({"lang": "r", "specs": [spec], "eco": "cran", "opts": {}})
    return False, "substrate predates the vocabulary", {}


def test_github_spec_goes_to_the_cran_lane(monkeypatch):
    """`owner/repo@ref` is a cran-lane spec, not a bespoke installer."""
    rec = _run(monkeypatch, source="github", package="org/pkg", ref="v1.2.3")
    cran = [i for i in rec.installs if i["eco"] == "cran"]
    assert cran, "github install never reached the cran lane"
    assert cran[0]["specs"] == ["org/pkg@v1.2.3"], cran[0]["specs"]
    assert not rec.installers, (
        "github went to the bespoke installer — that lane refuses on an adopted "
        f"base: {rec.installers}")


def test_github_without_ref_omits_the_at(monkeypatch):
    rec = _run(monkeypatch, source="github", package="org/pkg")
    cran = [i for i in rec.installs if i["eco"] == "cran"]
    assert cran[0]["specs"] == ["org/pkg"], cran[0]["specs"]


def test_github_falls_back_when_the_substrate_declines(monkeypatch):
    """An older substrate doesn't know git specs — we must still try, and fall
    back rather than leaving the user with nothing."""
    rec = _run(monkeypatch, source="github", package="org/pkg", ref="main", cran_ok=False)
    assert [i for i in rec.installs if i["eco"] == "cran"], "must TRY the cran lane first"
    assert rec.installers, "no fallback after the cran lane declined"
    assert "install_github" in rec.installers[0]["cmd"]


def test_every_installer_fallback_declares_its_write_target(monkeypatch):
    """`writes_to='rlib'` is what lets a declared installer run over a read-only
    base. An undeclared one is refused there, so the fallback is useless without
    it."""
    for src, pkg in (("github", "org/pkg"), ("bioconductor", "SomePkg"), ("cran", "somepkg")):
        rec = _run(monkeypatch, source=src, package=pkg, cran_ok=False)
        for inst in rec.installers:
            assert inst["writes_to"] == "rlib", (
                f"{src} fallback installer does not declare writes_to: {inst}")


def test_install_forwards_and_records_substrate_options(monkeypatch):
    """`cran_repos=[url]` must reach the substrate verb AND be recorded, or a
    rebuilt session replays a request that resolves against different repos.

    monkeypatch (not manual save/restore): these are module-level attributes on
    shared compute modules, and hand-rolled teardown leaked into whatever file
    ran next in the same process — test_lazy_session_lane's flip assertion went
    red only when it followed this one.
    """
    from core.compute import project_env
    import core.compute.adapter as _adapter
    import core.compute.named_envs as _ne
    seen: dict = {}
    row = {"additions": [], "rev": 0}

    class _Ad:
        def session_install(self, sid, **kw):
            seen.update(kw)
            return {"runtime": {"prefix": "/tmp/p", "direct_exec": True}}

    monkeypatch.setattr(_adapter, "get_compute", lambda: _Ad())
    monkeypatch.setattr(_ne, "_sync", lambda x: x)
    monkeypatch.setattr(project_env, "ensure",
                        lambda pid, lang: {"session_id": "s1", "runtime": {}})
    monkeypatch.setattr(project_env, "get", lambda pid, lang: row)
    monkeypatch.setattr(project_env, "_save_row", lambda pid, lang, r: row.update(r))

    project_env.install("p", "r", ["pkg"], eco="cran",
                        cran_repos=["https://example.org/repo"])
    assert seen.get("cran") == ["pkg"]
    assert seen.get("cran_repos") == ["https://example.org/repo"], seen
    assert row["additions"][-1]["opts"] == {"cran_repos": ["https://example.org/repo"]}
