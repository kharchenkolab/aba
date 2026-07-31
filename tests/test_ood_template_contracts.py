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


def _ladder_block(path: Path) -> str:
    """The instance-ladder fallback between its marker comments."""
    m = re.search(r">>> instance-ladder fallback(.*?)<<< instance-ladder fallback",
                  path.read_text(), re.S)
    assert m, f"{path.name} has lost its instance-ladder fallback markers"
    # Drop the marker lines' own trailing prose; compare the Ruby literal only.
    return "\n".join(l for l in m.group(1).splitlines() if "def_instances" in l or "'id' =>" in l)


def test_the_two_templates_fall_back_to_the_SAME_instance_ladder():
    """form.yml.erb renders the Instance menu; submit.yml.erb turns the chosen id
    into `-c <cores> --mem=<mem>`. Both prefer site.yaml's ladder, and both carry
    a built-in fallback for a deployment that ships no site config.

    Those two fallbacks must agree. If they drift, a bare deployment shows one
    size on the card and SUBMITS another — and nothing fails, because each file
    is independently valid. Same class as the card advertising a download whose
    bytes are missing: two halves of one contract, verified apart."""
    form = _ladder_block(APP / "form.yml.erb")
    submit = _ladder_block(APP / "submit.yml.erb")
    assert form.strip(), "the form's fallback ladder came back empty — marker drift"
    assert form == submit, (
        "the fallback instance ladders have diverged; a site-config-less "
        f"deployment would display one size and submit another:\n"
        f"--- form.yml.erb\n{form}\n--- submit.yml.erb\n{submit}")


def test_dashboard_side_templates_that_resolve_site_yaml_are_pinned():
    """`/cluster/aba` is the upstream default share root; a site deploy script
    rewrites it (install/ood/README.md, "Site deployer contract") — but it seds
    a FIXED LIST of files.

    The list matters only for the DASHBOARD-side templates. They render before
    any of this app's shell has run, so nothing has exported ABA_SHARE yet and
    the literal in the file is the only thing that resolves site.yaml. Miss one
    and it reads a config that isn't there, then degrades to the shipped
    fallback instead of erroring — submit.yml.erb would launch the portable
    instance ladder rather than the site's, silently.

    Pinned, so adding one is the reminder to extend the deployer's sed list."""
    readers = {f.name for f in sorted(APP.glob("*.yml.erb"))
               if "ABA_SITE_CONFIG" in f.read_text()}
    expected = {"form.yml.erb", "submit.yml.erb"}
    assert readers == expected, (
        f"dashboard-side site.yaml readers changed: added={sorted(readers - expected)} "
        f"removed={sorted(expected - readers)}. Extend the site deploy script's "
        "share-root rewrite list, then update this test.")
    for name in sorted(readers):
        assert "/cluster/aba/site.yaml" in (APP / name).read_text(), (
            f"{name} resolves site.yaml but not via the rewritable "
            "'/cluster/aba/site.yaml' literal, so the deployer's sed cannot reach it")


def test_node_side_scripts_take_the_share_root_from_the_environment():
    """The counterpart: the `template/` scripts run on the compute node, AFTER
    before.sh.erb has exported ABA_SHARE / ABA_SITE_CONFIG. They are therefore
    NOT on the deployer's rewrite list — which is only safe while every mention
    of the default is overridable. A bare `/cluster/aba` in one of them would be
    an unrewritten hardcode pointing at a path the site does not have."""
    offenders = []
    for f in sorted((APP / "template").rglob("*")):
        if not f.is_file():
            continue
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if "/cluster/aba" not in line or line.lstrip().startswith("#"):
                continue
            if ":-" not in line.split("/cluster/aba")[0][-40:]:
                offenders.append(f"{f.relative_to(APP)}:{i}: {line.strip()}")
    assert not offenders, (
        "node-side script hardcodes the default share root with no ${VAR:-…} "
        "override, and the deployer does not rewrite these files:\n"
        + "\n".join(offenders))


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
