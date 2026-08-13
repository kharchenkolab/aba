"""aba must not hard-require a weft kwarg that older substrates lack.

aba and weft upgrade on independent cadences. A SIF deploy bakes weft in, so the
two always match there — but a cluster-personal install (`~/.aba`) updates aba
code and weft separately, so aba routinely runs against a weft older than the
one it was written for.

`verify=` on `session_install` is the live instance. weft added it deliberately
backward-compatibly ("no verify => byte-identical, zero oracle invocations"), so
OMITTING it against an old substrate is safe and losing verification is the
correct degradation. aba passed it anyway, from two call sites: one wrapped in
`except TypeError:  # substrate predates verify=`, the other bare. The bare one
(`project_env.py`, the named-env install branch) died with

    TypeError: Weft.session_install() got an unexpected keyword argument 'verify'

raised from inside a thread pool, so the user saw a stack trace rather than an
install. Reproduced by `regtest/harness/env_check.py --r` against a ~/.aba weft
predating weft 75b2f6b; no agent involved.

The per-call-site `try/except` is the shape CLAUDE.md says to replace with a
property: a third call site added tomorrow forgets again. So the compat lives at
the ONE chokepoint every substrate call passes through, and this guards the
property there — including the other side, that an unknown kwarg still raises
rather than being silently swallowed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.compute import adapter as ad  # noqa: E402


class _OldWeft:
    """A substrate that REFUSES what the real old weft refuses. Per CLAUDE.md
    failure mode (b), a fake that merely ignores the kwarg would bless the bug —
    the real one raises TypeError, so this one does too."""

    def __init__(self):
        self.calls = []

    def session_install(self, session_id, /, **kw):
        if "verify" in kw:
            raise TypeError(
                "Weft.session_install() got an unexpected keyword argument 'verify'")
        self.calls.append(kw)
        return {"ok": True, "kw": dict(kw)}

    def some_other_verb(self, /, **kw):
        if "made_up" in kw:
            raise TypeError(
                "Weft.some_other_verb() got an unexpected keyword argument 'made_up'")
        return {"ok": True}


class _NewWeft(_OldWeft):
    """Accepts verify — the substrate aba was written against."""

    def session_install(self, session_id, /, **kw):
        self.calls.append(kw)
        return {"ok": True, "kw": dict(kw)}


def _adapter(weft):
    a = object.__new__(ad.WeftAdapter)
    a._weft = weft
    a._pool = None
    return a


async def _call(a, name, *args, **kw):
    return await a._call(name, *args, **kw)


@pytest.mark.parametrize("weft_cls", [_OldWeft, _NewWeft])
def test_session_install_works_on_both_substrates(monkeypatch, weft_cls):
    """THE PROPERTY. The same aba call must succeed whether or not the substrate
    understands `verify` — dropping it on the old one, keeping it on the new."""
    import asyncio

    from core import projects as _projects

    async def _in_pool(_pool, fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(_projects, "in_pool", _in_pool)
    w = weft_cls()
    a = _adapter(w)
    out = asyncio.run(_call(a, "session_install", "sid-1", pypi=["praise"], verify={"import": "praise"}))
    assert out.get("ok") is True, f"{weft_cls.__name__}: call did not succeed: {out!r}"
    assert w.calls, "the substrate was never actually called"
    got_verify = "verify" in w.calls[-1]
    assert got_verify is (weft_cls is _NewWeft), (
        f"{weft_cls.__name__}: verify passed={got_verify}; it must be dropped only "
        f"for a substrate that cannot take it")


def test_an_unknown_kwarg_still_raises(monkeypatch):
    """THE OTHER SIDE. A blanket 'retry without whatever it rejected' would turn
    every typo and every genuinely-required argument into a silent no-op. Only
    kwargs declared safe to omit may be dropped."""
    import asyncio

    from core import projects as _projects

    async def _in_pool(_pool, fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(_projects, "in_pool", _in_pool)
    a = _adapter(_OldWeft())
    with pytest.raises(TypeError, match="made_up"):
        asyncio.run(_call(a, "some_other_verb", made_up=1))


def test_the_drop_is_declared_not_inferred():
    """The safe-to-omit set is a short, reviewed list — not 'anything weft has
    not heard of'. Its membership is a compatibility CLAIM (weft guarantees
    `no verify => byte-identical`), so it belongs in code review, not in a
    heuristic."""
    safe = getattr(ad, "SUBSTRATE_OPTIONAL_KWARGS", None)
    assert safe is not None, "no declared set of omittable substrate kwargs"
    assert "verify" in safe
    assert len(safe) <= 4, f"the omittable set is growing unreviewed: {safe!r}"
