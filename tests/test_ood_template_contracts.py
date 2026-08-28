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

# The share root the card ships with, and the declared list of files whose copy
# of it a site deploy script must rewrite.
DEFAULT_SHARE = "/cluster/aba"
REWRITE_LIST = OOD / "site-rewrite.list"


def _rewrite_list() -> "list[str]":
    return [ln.strip() for ln in REWRITE_LIST.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _mentions_of_default_share() -> "list[tuple[str, int, str]]":
    """(relpath, lineno, line) for every non-comment mention of DEFAULT_SHARE."""
    out = []
    for f in sorted(APP.rglob("*")):
        if not f.is_file():
            continue
        try:
            text = f.read_text()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if DEFAULT_SHARE in line and not line.lstrip().startswith("#"):
                out.append((str(f.relative_to(APP)), i, line.strip()))
    return out


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
    an unrewritten hardcode pointing at a path the site does not have.

    before.sh.erb is EXCLUDED, and the exclusion is the point. It is where
    ABA_SHARE is defined, so its `${ABA_SHARE:-…}` is not an override of an
    earlier value — on an ordinary launch nothing set one, and the literal is
    the value. Scanning it here scored it "safe" on the `:-` alone and so
    reported on the one node-side file that genuinely must be rewritten. It is
    covered by test_the_site_rewrite_list_is_complete_and_exact instead."""
    offenders = []
    for f in sorted((APP / "template").rglob("*")):
        if not f.is_file() or f.name == "before.sh.erb":
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


def test_the_site_rewrite_list_is_complete_and_exact():
    """install/ood/site-rewrite.list names every card file whose default share
    root a deploy script must rewrite, and the deploy script reads that list
    instead of carrying its own copy.

    WHAT THIS GUARDS. The list used to live only as three paths inside a
    `sed -i` in a site-private shell script, and the failure it protects against
    is the one its own comment describes: "Miss one and the card reads a
    site.yaml that isn't there" — then degrades to the shipped portable ladder
    instead of erroring, so the card launches and is quietly wrong.

    Two of the three were pinned by the test above this one. The third,
    template/before.sh.erb, was not, and could not have been: the node-side scan
    treats a `${VAR:-default}` as safe, which is right for a script that READS
    ABA_SHARE and wrong for the one that DEFINES it.

    The property is a partition with no heuristic in it. Every non-comment
    mention of the default share root is either (a) on the list, or (b) in a
    template/ script that runs after before.sh.erb has exported the value, in
    which case it must be written as an env-overridable `${VAR:-…}`."""
    listed = _rewrite_list()
    assert listed, "site-rewrite.list is empty — the deploy script would rewrite nothing"
    for rel in listed:
        assert (APP / rel).is_file(), f"site-rewrite.list names {rel}, which does not exist"

    # (b) only holds because before.sh.erb is itself rewritten.
    assert "template/before.sh.erb" in listed, (
        "template/before.sh.erb is not on the rewrite list, so ABA_SHARE is "
        "defined from the shipped default and every later template/ script "
        "inherits the wrong root — the exemption the node-side test relies on "
        "no longer holds")

    mentions = _mentions_of_default_share()
    assert mentions, (
        f"no file mentions {DEFAULT_SHARE} any more — the shipped default "
        "changed and this contract needs rewriting, not deleting")

    offenders, unoverridable = [], []
    for rel, lineno, line in mentions:
        if rel in listed:
            continue
        if rel.startswith("template/"):
            # Runs after before.sh.erb; must take the value from the env.
            if ":-" not in line.split(DEFAULT_SHARE)[0][-40:]:
                unoverridable.append(f"{rel}:{lineno}: {line}")
            continue
        offenders.append(f"{rel}:{lineno}: {line}")
    assert not offenders, (
        f"these files use {DEFAULT_SHARE} but are not on "
        "install/ood/site-rewrite.list, and do not run after before.sh.erb has "
        "exported ABA_SHARE — a site deploy would leave them pointing at a path "
        "the site does not have:\n" + "\n".join(offenders))
    assert not unoverridable, (
        "these run after before.sh.erb but hardcode the default with no "
        "${VAR:-…} override:\n" + "\n".join(unoverridable))

    # …and nothing stale: a listed file that no longer mentions the default is a
    # rewrite the deploy script performs against nothing.
    mentioned = {rel for rel, _, _ in mentions}
    stale = [rel for rel in listed if rel not in mentioned]
    assert not stale, (
        f"site-rewrite.list names files that no longer mention {DEFAULT_SHARE}: "
        f"{stale} — drop them, or the deploy script rewrites nothing there")


def test_session_tmpdir_prefers_node_local_and_cleans_fallback():
    """The ENOSPC fix contract: TMPDIR prefers $SLURM_TMPDIR (node-local,
    Slurm-purged); the parallel-FS fallback is per-session and removed by the
    handler ACTUALLY installed on EXIT (no quota debris).

    Behavioral, not textual: a standalone ``trap 'rm -rf …' EXIT`` is unsafe
    because a LATER ``trap … EXIT`` silently replaces it (bash keeps one EXIT
    handler). So we resolve the last-installed EXIT handler and require the
    fallback cleanup to live inside it.

    The contract is now split across two files and BOTH halves must hold: the
    shared launch contract chooses and forwards TMPDIR (aba_launch.sh, sourced by
    the card and by the deployment gate alike), and the card owns the EXIT
    handler that removes the fallback. Checking only one file would let the other
    half rot silently.
    """
    launch = (APP / "template" / "aba_launch.sh").read_text()
    text = (APP / "template" / "script.sh.erb").read_text()
    assert "SLURM_TMPDIR" in launch, "TMPDIR must prefer node-local job scratch"
    assert re.search(r'--env "TMPDIR=', launch), "TMPDIR must be forwarded into the container"
    assert "ABA_LAUNCH_TMP_CLEANUP" in text, (
        "the card must take ownership of the fallback dir the contract created")
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


def _marker_literals(path: Path, pattern: str) -> set[str]:
    """The enrollment-marker list a file carries, as a set of literals."""
    m = re.search(pattern, path.read_text())
    assert m, f"{path.name} has lost its enrollment-marker list"
    return set(re.findall(r"""['"](\.[a-z-]+)['"]""", m.group(1)))


def test_every_reader_of_the_enrollment_marker_agrees():
    """"Is this lab enrolled?" is asked in three places, in two languages.

    `aba_preflight.py` gates the launch on it, `enroll_group.py` writes it, and
    `form.yml.erb` decides whether the lab is even OFFERED on the launch form.
    The Ruby one cannot import the Python one, so the list is copied — and a
    copy that drifts fails in the worst direction available: enrolment
    "succeeds", preflight agrees, and the lab simply never appears on the form,
    with no error anywhere to explain why.

    Same shape as the instance-ladder pair above: two halves of one contract,
    each independently valid, verified apart. Compared as SETS — order is not
    part of the meaning, since every reader asks `any(marker exists)`."""
    preflight = _marker_literals(OOD / "aba_preflight.py",
                                 r"OURS_MARKERS\s*=\s*\((.*?)\)")
    enroll = _marker_literals(OOD / "enroll_group.py",
                              r"OURS_MARKERS\s*=\s*\((.*?)\)")
    form = _marker_literals(APP / "form.yml.erb",
                            r"markers\s*=\s*\[(.*?)\]")
    assert preflight, "aba_preflight's marker list parsed empty — pattern drift"
    assert preflight == enroll == form, (
        "the enrollment-marker lists have diverged; a lab could be enrolled and "
        "still never appear on the launch form:\n"
        f"  aba_preflight.py : {sorted(preflight)}\n"
        f"  enroll_group.py  : {sorted(enroll)}\n"
        f"  form.yml.erb     : {sorted(form)}")


def test_aba_env_block_is_safe_to_source_under_set_e():
    """aba-env.sh must exit 0 whatever was optional in it.

    A sourced file's exit status is its LAST command's, and the generator ends
    with an optional ``[ -f <group>/.env ] && …`` chain. Where a group carries no
    .env that status is 1, so any consumer running under ``set -e`` dies —
    silently, with no message, at the instant it finished loading its environment
    CORRECTLY. The OOD card never noticed because before.sh.erb does not set -e;
    the deployment gate does, and it exited before printing a single line.

    Behavioral, not textual: generate the block for a group with NO .env (the
    failing shape) and actually source it under `set -euo pipefail`."""
    import subprocess
    import tempfile
    src = (APP.parent / "aba_preflight.py").read_text()
    assert 'lines.append("true' in src, (
        "the generated env block no longer ends in an unconditional success")

    # The degenerate shape: the optional chain present, its target ABSENT.
    with tempfile.TemporaryDirectory() as d:
        env_sh = Path(d) / "aba-env.sh"
        env_sh.write_text(
            "export ABA_RUNTIME_DIR=/somewhere\n"
            f"[ -f {d}/nonexistent/.env ] && set -a && . {d}/nonexistent/.env && set +a\n"
            "true   # aba-env.sh loaded\n")
        r = subprocess.run(
            ["/usr/bin/env", "bash", "-c", f'set -euo pipefail; . "{env_sh}"; echo LOADED'],
            capture_output=True, text=True)
        assert r.returncode == 0 and "LOADED" in r.stdout, (
            "the env block kills a `set -e` consumer when the group .env is absent")

        # ARMED: the same block WITHOUT the terminator must fail, or the check above
        # would pass on any file at all.
        env_sh.write_text(
            "export ABA_RUNTIME_DIR=/somewhere\n"
            f"[ -f {d}/nonexistent/.env ] && set -a && . {d}/nonexistent/.env && set +a\n")
        r2 = subprocess.run(
            ["/usr/bin/env", "bash", "-c", f'set -euo pipefail; . "{env_sh}"; echo LOADED'],
            capture_output=True, text=True)
        assert r2.returncode != 0, (
            "PRECONDITION: the unterminated block did NOT fail, so this test "
            "cannot tell whether the terminator is doing anything")
