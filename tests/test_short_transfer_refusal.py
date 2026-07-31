"""No transfer path may install or return a SHORT payload as if it were whole.

The class, from the 2026-07-27 viewer incident: a chunked/capped transfer decides
it is finished from the substrate's own `eof`/`capped`/`truncated` FLAGS, and
installs whatever it has. When a substrate clamps without setting the flag — or
sets `eof` at the clamp boundary — the result is a short payload presented as
complete. It is then sticky (cached, stamped, harvested) and indistinguishable
downstream from real data, which makes it strictly worse than a failed fetch.

Three sites carried it besides the one that broke:

  * `_fetch_remote_view_file`'s ranged read — the buffer is handed to the agent
    AS the artifact, so a half-read figure is something it reasons about;
  * `_fetch_new_kernel_files` — a short file is HARVESTED as the Run's output;
  * `_materialize_file`'s preview branch — the copy is stamped with the digest
    and treated as current, poisoning every later read.

Each already knew the expected size (a preceding stat, the registry row, or the
locate record) and checked only the flag. Every one now verifies the byte count.

This file guards the PROPERTY over the assembled behaviour, not one instance:
each case drives a real entry point with a substrate that clamps silently, and
asserts the caller REFUSES. The static half below is what catches the next
transfer path added without a check.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


# ── the remote view path: a short read must not become "the artifact" ────────

PAYLOAD = bytes(range(64)) * 4          # 256 bytes


def _view_path(monkeypatch, *, reply):
    """Drive `_read_remote_abs_file` against a fake data plane. `reply(offset)`
    returns the envelope for that offset."""
    import base64  # noqa: F401 — used by the callers' reply builders
    from core.compute import retention, adapter
    from content.bio.mcp_servers.aba_core.tools import entity_ops

    class _C:
        def sync_call(self, verb, *a, **kw):
            assert verb == "data_register"
            return {"ref": "sha256:deadbeef", "bytes": len(PAYLOAD)}

    monkeypatch.setattr(adapter, "get_compute", lambda: _C())
    monkeypatch.setattr(retention, "data_read_range",
                        lambda ref, rel=None, *, offset=0, length=None, site=None:
                        reply(offset))
    return entity_ops._read_remote_abs_file("/x/f.png", "siteA")


def _envelope(offset, *, cap, total=len(PAYLOAD), flags="honest"):
    import base64
    body = PAYLOAD[offset:offset + cap]
    env = {"ref": "r", "offset": offset, "nbytes": len(body), "size": total,
           "bytes_b64": base64.b64encode(body).decode()}
    done = offset + len(body) >= total
    if flags == "honest":
        env["eof"], env["capped"] = done, not done
    elif flags == "eof_at_cap":
        env["eof"], env["capped"] = True, not done      # lies at every clamp
    elif flags == "no_capped":
        env["eof"] = done                                # `capped` absent
    return env


def test_remote_view_refuses_a_read_that_stops_short(monkeypatch):
    """THE regression at this site. A substrate that returns 8 of 256 bytes and
    says eof=True. Returning those 8 bytes would show the agent a truncated
    figure as if it were the whole one — something it then reasons about."""
    data, note = _view_path(monkeypatch,
                            reply=lambda o: _envelope(o, cap=8, flags="eof_at_cap"))
    assert data is None, f"returned {len(data)} bytes as the artifact"
    assert "short read" in note and "8 of 256" in note, note


def test_remote_view_assembles_whole_when_capped_is_MISSING(monkeypatch):
    """The other clamp shape: it keeps reading because the stated size says to."""
    data, note = _view_path(monkeypatch,
                            reply=lambda o: _envelope(o, cap=8, flags="no_capped"))
    assert data == PAYLOAD, f"got {0 if data is None else len(data)} of {len(PAYLOAD)}"


def test_remote_view_still_works_on_an_honest_substrate(monkeypatch):
    """CEILING: the ordinary multi-loop read must be untouched."""
    data, note = _view_path(monkeypatch,
                            reply=lambda o: _envelope(o, cap=32, flags="honest"))
    assert data == PAYLOAD and "fetched from siteA" in note


def test_remote_view_single_shot_read_is_unaffected(monkeypatch):
    """CEILING / degenerate: the whole file in one reply — the common small-file
    case, which must not now require a second round trip or trip the check."""
    calls: list = []

    def once(o):
        calls.append(o)
        return _envelope(o, cap=len(PAYLOAD), flags="honest")

    data, _ = _view_path(monkeypatch, reply=once)
    assert data == PAYLOAD and len(calls) == 1


def test_kernel_harvest_verifies_against_the_stat():
    src = (ROOT / "backend/content/bio/tools/run_exec.py").read_text()
    i = src.index("def _fetch_new_kernel_files")
    body = src[i:i + 6000]
    assert 'st.get("bytes")' in body and "len(data) != _want" in body, \
        "kernel harvest no longer verifies the fetched length against the stat"


def test_local_copy_verifies_against_the_located_size():
    src = (ROOT / "backend/content/bio/lifecycle/runs.py").read_text()
    assert "len(_bytes) != size" in src, \
        "_materialize_file no longer verifies the preview length"


# ── the static half: a new transfer path must verify, not just check a flag ──
#
# Matches the ASSEMBLY shape — a loop or decode that writes/returns bytes — and
# requires a length comparison nearby. Kept deliberately narrow (the four known
# transfer sites) so it is a real gate rather than a whole-repo grep that would
# be silenced with a noqa on its first false positive.

_SITES = [
    ("backend/core/viewers/range_cache.py", "_fetch_and_cache", "member_size"),
    ("backend/content/bio/mcp_servers/aba_core/tools/entity_ops.py",
     "_read_remote_abs_file", "stated"),
    ("backend/content/bio/tools/run_exec.py", "_fetch_new_kernel_files", "_want"),
    ("backend/content/bio/lifecycle/runs.py", "_materialize_file", "len(_bytes)"),
]


def _fn_body(path: str, fn: str) -> str:
    src = (ROOT / path).read_text()
    m = re.search(rf"^def {re.escape(fn)}\b", src, re.M) or \
        re.search(rf"^\s+def {re.escape(fn)}\b", src, re.M)
    assert m, f"{path}: {fn} not found — the scanner is stale, not the code"
    return src[m.start():m.start() + 8000]


def test_the_scanner_finds_every_declared_site():
    """ARMED: a scanner that cannot locate its subjects reports every site
    compliant. This is the check that fails when a function is renamed."""
    for path, fn, _needle in _SITES:
        assert len(_fn_body(path, fn)) > 200, f"{path}:{fn}"


@pytest.mark.parametrize("path,fn,needle", _SITES)
def test_every_transfer_site_compares_a_length(path, fn, needle):
    body = _fn_body(path, fn)
    assert needle in body, (
        f"{path}:{fn} no longer compares the assembled length against the "
        f"expected size — a clamped substrate would install a short payload")


def test_no_transfer_site_breaks_on_capped_alone():
    """The precise anti-pattern: `if ... not r.get("capped")` as the sole exit,
    with no size in the condition. That single line is what shipped a 16 MiB
    truncation into three stores."""
    bad = []
    for path, fn, _n in _SITES:
        body = _fn_body(path, fn)
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("if ") and 'not r.get("capped")' in s and "size" not in s \
                    and "stated" not in s and "member_size" not in s:
                # tolerated only when guarded by a "size is unknown" clause
                if "is None" not in s:
                    bad.append(f"{path}:{fn}: {s}")
    assert not bad, "\n".join(bad)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── every harvest names its project explicitly ───────────────────────────────
#
# `harvest_artifacts` falls back to `current_project_id()` — the AMBIENT project,
# which a concurrent turn moves mid-call. Live (2026-07-27, --cross-project lane):
# a thread in prj_A had its table registered at `/artifacts/prj_B/…`, so the
# producing project showed no output at all while a bystander gained one. The exec
# records were correct by then; the ARTIFACT COPY was not, because four call sites
# omitted the argument they already had in scope.
#
# Same shape as the transfer sites above: the caller knows, and must say.

_HARVEST_RE = __import__("re").compile(r"harvest_artifacts\s*\(")


def _harvest_calls() -> list[tuple[str, int, str]]:
    out = []
    for sub in ("backend/core", "backend/content"):
        for f in sorted((ROOT / sub).rglob("*.py")):
            if "vendor" in f.parts or "__pycache__" in f.parts:
                continue
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i, line in enumerate(lines):
                code = line.split("#", 1)[0]
                if not _HARVEST_RE.search(code) or "def harvest_artifacts" in code:
                    continue
                # the call may span lines; look at the whole invocation window
                window = "\n".join(lines[i:i + 6])
                out.append((str(f.relative_to(ROOT)), i + 1, window))
    return out


def test_the_harvest_scanner_finds_call_sites():
    """ARMED: a scanner that matches nothing certifies every site compliant."""
    calls = _harvest_calls()
    assert len(calls) >= 6, f"only found {len(calls)} harvest call sites"


def test_every_harvest_call_passes_a_project():
    missing = [f"{p}:{n}" for p, n, w in _harvest_calls() if "project_id" not in w]
    assert not missing, (
        "harvest_artifacts without an explicit project_id — the copies land under "
        "whatever project is ambient at that instant:\n  " + "\n  ".join(missing))
