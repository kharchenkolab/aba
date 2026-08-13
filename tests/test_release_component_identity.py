"""A component id must name ONE artifact.

`ensure_component` is a build-or-reuse cache keyed by `cid`: if
`components/<kind>/<cid>` exists it returns immediately, no copy. That is
correct *only* while the id is derived from what the component contains.

aba-vbc's deploy.sh keyed the `sif` component on the aba git short sha. The
recipe pack, weft, the base image and the node toolchain are all baked into
that image and none of them move the sha — so a rebuild produced different
bytes under an id that already existed, `ensure_component` short-circuited,
`compose_release` re-linked the OLD image, and the deploy printed a success
line naming a release that serves the previous build. The only signal was the
word "reused" in a JSON blob nobody reads. Live consequence: the documented
"new recipes -> build.sh && deploy.sh" upgrade lane was a no-op, and a
staging->prod promotion (which is BY CONSTRUCTION "stage the same id somewhere
else") would inherit it.

The engine cannot recompute a directory component's identity cheaply, and it
must not try: an `env` component is keyed on its LOCKFILE hash on purpose, so
reuse across differing bytes is the whole point there. But a single-FILE
component (the image) is cheap to check, and for it a collision is never
intentional. So `stage_release` refuses it rather than silently serving stale
bytes.
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core import release  # noqa: E402


def _share(tmp_path: Path) -> str:
    d = tmp_path / "share"
    d.mkdir()
    return str(d)


def _sif(tmp_path: Path, body: bytes, name: str = "aba-weft.sif") -> str:
    p = tmp_path / "build" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(body)
    return str(p)


def _served(share: str) -> bytes:
    """The bytes a session launched from `current` would actually read."""
    sif = next((Path(share) / "current" / "sif").iterdir())
    return sif.read_bytes()


def test_same_id_different_bytes_is_refused(tmp_path):
    """THE BUG. Second stage carries new bytes under the id the first one used."""
    share = _share(tmp_path)
    release.stage_release("v1", {"sif": ("abc1234", _sif(tmp_path, b"BUILD-1"))},
                          share=share, do_promote=True)
    assert _served(share) == b"BUILD-1"

    with pytest.raises(ValueError, match="already exists with different content"):
        release.stage_release("v2", {"sif": ("abc1234", _sif(tmp_path, b"BUILD-2"))},
                              share=share, do_promote=True)
    # …and the refusal must not have half-applied: what was promoted still serves.
    assert _served(share) == b"BUILD-1", "a refused stage still moved `current`"


def test_same_id_same_bytes_still_dedups(tmp_path):
    """THE OTHER SIDE. A guard that only checked the collision would pass if the
    engine simply stopped reusing anything — which would re-copy a multi-GB
    component on every code-only upgrade, the exact cost the cache exists to
    avoid. An identical re-stage must still be a no-copy reuse."""
    share = _share(tmp_path)
    release.stage_release("v1", {"sif": ("abc1234", _sif(tmp_path, b"SAME"))},
                          share=share)
    out = release.stage_release("v2", {"sif": ("abc1234", _sif(tmp_path, b"SAME"))},
                                share=share, do_promote=True)
    assert out["reused"] == ["sif/abc1234"]
    assert _served(share) == b"SAME"


def test_a_content_addressed_id_never_collides(tmp_path):
    """The deployer's half of the contract: key the id on the bytes and the
    refusal above is unreachable — two builds simply become two components."""
    share = _share(tmp_path)
    for body in (b"BUILD-1", b"BUILD-2"):
        src = _sif(tmp_path, body)
        cid = hashlib.sha256(Path(src).read_bytes()).hexdigest()[:12]
        release.stage_release(f"v-{cid}", {"sif": (cid, src)},
                              share=share, do_promote=True)
    assert _served(share) == b"BUILD-2"
    assert len(list((Path(share) / "components" / "sif").iterdir())) == 2


def test_a_directory_component_is_not_content_checked(tmp_path):
    """DEGENERATE / deliberate exemption. `env` is keyed on the lockfile hash,
    not on the built tree — reuse across differing bytes is the point, and
    hashing a multi-GB env on every deploy would be the wrong trade. The
    refusal must be scoped to single-file components, not applied by reflex."""
    share = _share(tmp_path)
    envdir = tmp_path / "base"
    (envdir / "aba-venv" / "bin").mkdir(parents=True)
    (envdir / "aba-venv" / "bin" / "python").write_text("v1")
    release.stage_release("v1", {"env": ("lock0001", str(envdir))}, share=share)
    (envdir / "aba-venv" / "bin" / "python").write_text("v2-DIFFERENT")
    out = release.stage_release("v2", {"env": ("lock0001", str(envdir))}, share=share)
    assert out["reused"] == ["env/lock0001"]


@pytest.mark.parametrize("first,second", [
    (b"", b"x"),                      # empty -> nonempty
    (b"x", b""),                      # nonempty -> empty
    (b"ab", b"ba"),                   # SAME LENGTH, different content
])
def test_collision_is_caught_whatever_shape_the_difference_takes(tmp_path, first, second):
    """WIDE: a size-only comparison is the tempting cheap check and it passes
    the same-length case, which is exactly what a rebuilt image looks like."""
    share = _share(tmp_path)
    release.stage_release("v1", {"sif": ("dup", _sif(tmp_path, first))}, share=share)
    with pytest.raises(ValueError, match="already exists with different content"):
        release.stage_release("v2", {"sif": ("dup", _sif(tmp_path, second))}, share=share)


def test_the_refusal_names_the_fix(tmp_path):
    """An operator hitting this at 2am needs to know it is an ID problem, not a
    disk problem. Name the component and say the id is not content-addressed."""
    share = _share(tmp_path)
    release.stage_release("v1", {"sif": ("abc1234", _sif(tmp_path, b"A"))}, share=share)
    with pytest.raises(ValueError) as ei:
        release.stage_release("v2", {"sif": ("abc1234", _sif(tmp_path, b"B"))}, share=share)
    msg = str(ei.value)
    assert "sif/abc1234" in msg
    assert "content" in msg.lower()
