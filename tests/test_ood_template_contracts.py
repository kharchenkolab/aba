"""OOD launcher app — shipped-file contracts (install/ood/README.md).

The app dir is consumed VERBATIM by bare deployments and enriched in flight
by site deploy repos. Two things must therefore hold in the repo itself:
shipped files render clean (no out-of-repo __TOKEN__ placeholders — a bare
deployment must never show template artifacts on the card), and the
in-session TMPDIR redirect prefers node-local job scratch with a cleaned-up
parallel-FS fallback (the ENOSPC fix must not trade a tmpfs overflow for
PFS quota debris). The card's icon is a third: OnDemand finds it by FILENAME
and degrades silently to generic gears when it cannot."""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
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


def test_the_card_ships_an_icon_ood_will_find_and_can_actually_render():
    """OnDemand resolves the card icon by FILENAME — `OodApp#icon_uri` serves
    `icon.svg`, else `icon.png`, else the manifest's `icon:`, else `fas://cog`.
    So a rename drops the card back to generic gears with nothing logged
    anywhere; the filename is the whole contract and is asserted first.

    The rest guards the ways an icon that IS found still renders wrong. The
    dashboard puts it in an ``<img src=…>`` (``icon_tag`` → ``image_tag``),
    which is a SEPARATE document: it inherits no color from the page and
    fetches no external resource. So `currentColor` — the natural thing to
    lift from the app's own `BrandIcon`, which is inline SVG and does inherit
    — silently resolves to black, and an external `href` never loads at all.
    And `.app-icon` is a SQUARE box at three sizes (100px card, 24px apps
    table, 14px navbar), so a non-square viewBox is distorted at every one."""
    svg, png = APP / "icon.svg", APP / "icon.png"
    assert svg.is_file() or png.is_file(), (
        "no icon.svg / icon.png in the OOD app root — OodApp#icon_uri falls "
        "through to fas://cog and the card shows generic gears")
    if not svg.is_file():
        pytest.skip("png-only icon: the SVG-specific rendering checks do not apply")

    text = svg.read_text()
    root = ET.fromstring(text)                   # must parse: OOD serves it as-is
    # Comments are not rendered, and this file's own comment EXPLAINS the
    # currentColor trap below — scanning them would fail on the explanation.
    markup = re.sub(r"<!--.*?-->", "", text, flags=re.S)

    box = root.get("viewBox")
    assert box, "icon.svg has no viewBox — it cannot scale to the three sizes"
    w, h = (float(v) for v in box.replace(",", " ").split()[2:4])
    assert abs(w - h) < 1e-6, (
        f"viewBox {box!r} is not square; .app-icon is a square box, so this "
        "renders distorted on the card, in the apps table and in the navbar")

    assert "currentColor" not in markup, (
        "currentColor in an <img>-embedded SVG resolves to black — it inherits "
        "nothing from the dashboard. Use literal colors here (the app's inline "
        "BrandIcon can use currentColor precisely because it is NOT an <img>).")

    external = [el.tag for el in root.iter()
                if el.get("href") or el.get("{http://www.w3.org/1999/xlink}href")]
    assert not external, (
        f"icon.svg references external resources {external} — an <img> document "
        "loads none of them, so those parts render blank")


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
