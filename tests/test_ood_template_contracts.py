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
    Slurm-purged); the parallel-FS fallback is per-session and removed by an
    exit trap (no quota debris)."""
    text = (APP / "template" / "script.sh.erb").read_text()
    assert "SLURM_TMPDIR" in text, "TMPDIR must prefer node-local job scratch"
    assert re.search(r"trap\s+'rm -rf .*_sess_tmp.*'\s+EXIT", text), \
        "the PFS fallback tmp dir must be cleaned by an EXIT trap"
    assert re.search(r'--env "TMPDIR=', text), "TMPDIR must be forwarded into the container"
