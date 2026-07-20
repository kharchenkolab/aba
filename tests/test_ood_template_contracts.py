"""OOD launcher app — shipped-file contracts (install/ood/README.md).

The app dir is consumed VERBATIM by bare deployments and enriched in flight
by site deploy repos. Two things must therefore hold in the repo itself:
shipped files render clean (no out-of-repo __TOKEN__ placeholders — a bare
deployment must never show template artifacts on the card), and the
in-session TMPDIR redirect prefers node-local job scratch with a cleaned-up
parallel-FS fallback (the ENOSPC fix must not trade a tmpfs overflow for
PFS quota debris)."""
from __future__ import annotations
import re
from pathlib import Path

import pytest

OOD = Path(__file__).resolve().parents[1] / "install" / "ood"
APP = OOD / "aba"

pytestmark = pytest.mark.platform

_TOKEN = re.compile(r"__[A-Z][A-Z0-9_]{2,}__")

# Tokens whose injector is SELF-CONTAINED in the shipped app (not an
# out-of-repo deploy script): __OOD_PREFIX__ lives in the frontend dist's
# built assets; script.sh.erb itself seds it at session runtime with the
# session's proxy prefix — the mentions in that script ARE the injector.
_SELF_CONTAINED = {"__OOD_PREFIX__"}


def test_shipped_app_files_carry_no_template_tokens():
    """No __TOKEN__ placeholders in any shipped app file: injectors live in
    site repos (insert-if-deploying), so a token here IS the rendered output
    on every bare deployment."""
    offenders = []
    for f in sorted(APP.rglob("*")):
        if not f.is_file():
            continue
        try:
            text = f.read_text()
        except UnicodeDecodeError:
            continue
        for m in _TOKEN.finditer(text):
            if m.group(0) in _SELF_CONTAINED:
                continue
            offenders.append(f"{f.relative_to(OOD)}: {m.group(0)}")
    assert not offenders, (
        "shipped OOD app files must render clean (site deploy scripts INSERT, "
        "never replace tokens — install/ood/README.md):\n" + "\n".join(offenders))


def test_session_tmpdir_prefers_node_local_and_cleans_fallback():
    """The ENOSPC fix contract: TMPDIR prefers $SLURM_TMPDIR (node-local,
    Slurm-purged); the parallel-FS fallback is per-session and removed by the
    handler ACTUALLY installed on EXIT (no quota debris).

    Behavioral, not textual: a standalone ``trap 'rm -rf …' EXIT`` is unsafe
    because a LATER ``trap … EXIT`` silently replaces it (bash keeps one EXIT
    handler). So we resolve the last-installed EXIT handler and require the
    fallback cleanup to live inside it."""
    text = (APP / "template" / "script.sh.erb").read_text()
    assert "SLURM_TMPDIR" in text, "TMPDIR must prefer node-local job scratch"
    assert re.search(r'--env "TMPDIR=', text), "TMPDIR must be forwarded into the container"
    # bash keeps a single EXIT handler — the LAST `trap … EXIT` wins.
    exit_traps = re.findall(r'^\s*trap\s+(.+?)\s+((?:\w+\s+)*EXIT)\b', text, re.M)
    assert exit_traps, "no EXIT trap found"
    handler = exit_traps[-1][0].strip().strip("'\"")
    body = handler                                   # inline handler: check its own text
    fn = re.search(rf'^{re.escape(handler)}\s*\(\)\s*\{{(.*?)\n\}}', text, re.S | re.M)
    if fn:                                           # named function: check its body
        body = fn.group(1)
    assert re.search(r'rm -rf\s+.*_sess_tmp', body), (
        "the fallback TMPDIR must be removed by the EXIT-installed handler "
        f"({handler!r}); a separate `trap 'rm -rf' EXIT` gets clobbered by a "
        "later `trap … EXIT` and leaks the per-session dir")
