"""One env store, per-deployment pins — the fix for the two-tree disaster.

Staging and production kept SEPARATE published env trees, and moving a tested
pack between them meant copying its squashfs image. A squashfs pack bakes its
own absolute prefix, so the copy only activates at the path it was built for.
On 2026-08-27 the copies in production carried staging paths: every session
env failed to activate, kernels died with 127, and because BOTH trees sit in
each site's `ro_roots` — and adoption resolves an EnvID across all roots —
the broken production copy was found first even by STAGING sessions. One bad
copy took down both deployments.

The store is now single and append-only; a deployment says which VERSION it
adopts. Promotion is moving production's pin to what staging proved.

Run: python tests/test_pack_pin.py   (or via pytest)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_pin_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "p.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.compute.seeding as seeding  # noqa: E402
from core import config  # noqa: E402


def _site(tmp_path, body: str) -> str:
    p = tmp_path / "site.yaml"
    p.write_text(body)
    return str(p)


class _Setting:
    def __init__(self, v): self._v = v
    def get(self): return self._v


def test_a_pinned_pack_adopts_that_version(monkeypatch, tmp_path):
    """THE point: the deployment chooses the artifact, not a mutable shared
    pointer that both deployments race for."""
    monkeypatch.setattr(config.settings, "site_config", _Setting(_site(tmp_path, """
envs:
  publish_tree: /shared/envs
  pin:
    python-bio: 2026.08.27-8d4389ba
    r-bio: 2026.08.27-654521e9
""")))
    assert seeding.pack_pin("python-bio") == "2026.08.27-8d4389ba"
    assert seeding.pack_pin("r-bio") == "2026.08.27-654521e9"


def test_an_unpinned_pack_still_takes_latest(monkeypatch, tmp_path):
    """ARMED the other way: pinning is opt-in. A deployment that says nothing
    must behave exactly as before, or every existing install breaks."""
    monkeypatch.setattr(config.settings, "site_config", _Setting(_site(tmp_path, """
envs:
  publish_tree: /shared/envs
""")))
    assert seeding.pack_pin("python-bio") == "latest"
    monkeypatch.setattr(config.settings, "site_config", _Setting(_site(tmp_path, """
envs:
  publish_tree: /shared/envs
  pin:
    r-bio: 2026.08.27-654521e9
""")))
    assert seeding.pack_pin("r-bio") == "2026.08.27-654521e9"
    assert seeding.pack_pin("python-bio") == "latest"   # sibling pin ≠ this one


def test_the_pin_actually_reaches_env_adopt(monkeypatch, tmp_path):
    """Behavioural, not a source grep: the version must arrive at the
    substrate call. `env_adopt` has always accepted `version=`; the bug was
    that nothing passed it."""
    monkeypatch.setattr(config.settings, "site_config", _Setting(_site(tmp_path, """
envs:
  pin: {python-bio: 2026.08.27-8d4389ba}
""")))
    monkeypatch.setattr(config.settings, "weft_publish_tree", _Setting("/shared/envs"))
    monkeypatch.setattr(config.settings, "weft_publish_site", _Setting("local"))
    seen: dict = {}

    class _Ad:
        def env_adopt(self, site, tree, name, version="latest"):
            seen.update(site=site, tree=tree, name=name, version=version)
            return {"env_id": "env:v1:deadbeef"}
    monkeypatch.setattr(seeding._adapter, "get_compute", lambda: _Ad())
    monkeypatch.setattr(seeding.named_envs, "_sync", lambda x: x)

    assert seeding.adopt_env_id("python-bio") == "env:v1:deadbeef"
    assert seen["version"] == "2026.08.27-8d4389ba", seen
    assert seen["tree"] == "/shared/envs"


def test_an_unreadable_site_file_degrades_to_latest_not_to_a_crash(monkeypatch, tmp_path):
    """WIDE — the degenerate config. A missing or malformed site.yaml must not
    take the deployment down; adoption falls back to `latest`, which is the
    behaviour every pre-pin install already had."""
    monkeypatch.setattr(config.settings, "site_config", _Setting(str(tmp_path / "nope.yaml")))
    assert seeding.pack_pin("python-bio") == "latest"
    bad = tmp_path / "bad.yaml"
    bad.write_text("envs: [this is not a mapping\n")
    monkeypatch.setattr(config.settings, "site_config", _Setting(str(bad)))
    assert seeding.pack_pin("python-bio") == "latest"
    monkeypatch.setattr(config.settings, "site_config", _Setting(""))
    assert seeding.pack_pin("python-bio") == "latest"


def _standalone() -> int:
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v):
            self._u.append((t, n, getattr(t, n, None))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in (test_a_pinned_pack_adopts_that_version,
              test_an_unpinned_pack_still_takes_latest,
              test_the_pin_actually_reaches_env_adopt,
              test_an_unreadable_site_file_degrades_to_latest_not_to_a_crash):
        mp = _MP()
        try:
            t(mp, Path(tempfile.mkdtemp()))
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(); print(f"  [FAIL] {t.__name__}: {e}"); rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
