"""The OOD card's templates, EXECUTED by a real Ruby the way OnDemand runs them.

The card's other tests read these files as text. That is how the
no-enrolled-lab branch shipped naming six permission groups and not the lab:
every line was present and plausible, and nothing executed it against an
account shaped like a real one — dozens of groups, the lab sorting last. It
was found by a person opening the form.

install/ood/render_card.rb renders (ERB trim_mode '-', then YAML), as
OnDemand's BatchConnect::App does. The account is faked the way the real calls
behave: Etc.getgrgid RAISES on a gid with no group entry, as it does on a real
node, rather than returning something convenient.

Needs `ruby` (ABA_RUBY or PATH); the form.js tests also need `node` (ABA_NODE
or PATH) and the frontend's happy-dom. Without them these SKIP, visibly — the
text-level contracts in test_ood_template_contracts.py still run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "install" / "ood" / "aba"
RENDER = REPO / "install" / "ood" / "render_card.rb"
HAPPY_DOM = REPO / "frontend" / "node_modules" / "happy-dom" / "lib" / "index.js"

RUBY = os.environ.get("ABA_RUBY") or shutil.which("ruby")
NODE = os.environ.get("ABA_NODE") or shutil.which("node")

pytestmark = pytest.mark.platform
needs_ruby = pytest.mark.skipif(
    not RUBY, reason="no ruby (set ABA_RUBY): the card's ERB cannot be executed")
needs_dom = pytest.mark.skipif(
    not (NODE and HAPPY_DOM.is_file()),
    reason="no node + frontend/node_modules/happy-dom: form.js cannot be run")

REFUSAL = "— not enrolled"
CONTACT = "the-admin"
# What a real account looks like: permission groups (VPN, licences, share
# ACLs) that sort BEFORE the lab, and a user-private group named for the user.
PERMISSION_GROUPS = (["all.users"] + [f"perm.share{i:02d}.rx" for i in range(14)]
                     + ["perm.vpn"])

# Etc.getgrgid is replaced AFTER `require 'etc'`: the C extension would
# otherwise redefine it over the fake when the template requires it.
_FAKE_ACCOUNT = r"""
require 'etc'; require 'json'
FAKE = JSON.parse(ENV.fetch('ABA_FAKE_ACCOUNT'))
module Process; def self.groups = FAKE['gids']; def self.gid = FAKE['primary']; end
module Etc
  def self.getgrgid(gid)
    name = FAKE['names'][gid.to_s] or raise ArgumentError, "can't find group for #{gid}"
    Struct.new(:name).new(name)
  end
end
"""


def _account(groups: "list[str]", primary: str) -> dict:
    names = {str(1000 + i): g for i, g in enumerate(groups)}
    gid = {g: int(k) for k, g in names.items()}
    # 99999 has no group entry — real accounts carry these (a deleted group).
    return {"primary": gid[primary], "gids": sorted(gid.values()) + [99999], "names": names}


class Card:
    """A site config + a groups tree under tmp, rendered as user `alice`."""

    def __init__(self, tmp: Path, *, labs_enabled: bool = True,
                 ui: "dict | None" = None, form: "dict | None" = None):
        self.tmp, self.groups = tmp, tmp / "groups"
        self.groups.mkdir(parents=True)
        site = {"scopes": {"group": {"enabled": labs_enabled,
                                     "root_path": f"{self.groups}/{{group_dir}}/aba",
                                     "strip_suffix": ".grp"}},
                "ui_text": {"enroll_contact": CONTACT, **(ui or {})}}
        if form:
            site["form"] = form
        # JSON is YAML — no quoting of the fixture's own to get wrong.
        (tmp / "site.yaml").write_text(json.dumps(site))
        (tmp / "fake_account.rb").write_text(_FAKE_ACCOUNT)

    def lab_folder(self, *groups: str, enrolled: bool = False) -> "Card":
        for g in groups:
            d = self.groups / g.removesuffix(".grp")
            d.mkdir()
            if enrolled:
                (d / "aba").mkdir()
                (d / "aba" / ".aba-workspace").touch()
        return self

    def rendered(self, account: dict) -> dict:
        """The whole rendered form — asserting it rendered AND parsed as YAML."""
        env = {**os.environ, "ABA_SITE_CONFIG": str(self.tmp / "site.yaml"),
               "USER": "alice", "HOME": str(self.tmp / "home"),
               "ABA_FAKE_ACCOUNT": json.dumps(account)}
        r = subprocess.run([RUBY, "-r", str(self.tmp / "fake_account.rb"), str(RENDER),
                            "form", str(APP / "form.yml.erb")],
                           capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0, f"form.yml.erb did not render (rc={r.returncode}):\n{r.stderr}"
        return json.loads(r.stdout)

    def form(self, account: dict) -> dict:
        """The rendered Lab field."""
        return self.rendered(account)["attributes"]["aba_lab"]

    def submit(self, values: dict) -> subprocess.CompletedProcess:
        env = {**os.environ, "ABA_SITE_CONFIG": str(self.tmp / "site.yaml")}
        return subprocess.run([RUBY, str(RENDER), "submit", str(APP / "submit.yml.erb"),
                               json.dumps(values)],
                              capture_output=True, text=True, env=env, timeout=60)


# ── form.yml.erb: what the Lab field says ────────────────────────────────────

@needs_ruby
def test_an_unenrolled_user_is_told_their_LAB_not_their_permission_groups(tmp_path):
    """The reported case, on the account shape it happened on."""
    card = Card(tmp_path).lab_folder("zeta.grp")
    acct = _account(PERMISSION_GROUPS + ["zeta.grp", "alice"], primary="zeta.grp")
    # PRECONDITION — the regime the bug lived in: at least six groups sort
    # before the lab. A fixture where the lab sorts first cannot see it.
    assert sorted(acct["names"].values()).index("zeta.grp") >= 6

    (label, value), = card.form(acct)["options"]
    assert value == "", f"the refusal carries a value, so it submits: {value!r}"
    assert label == f"{REFUSAL}: ask {CONTACT} to enrol zeta.grp", label
    named = [g for g in PERMISSION_GROUPS if g in label]
    assert not named, f"the refusal names groups no one could enrol: {named}"


@needs_ruby
def test_several_labs_the_primary_comes_first_and_the_rest_are_COUNTED(tmp_path):
    labs = ["alpha.grp", "beta.grp", "gamma.grp", "omega.grp", "zeta.grp"]
    card = Card(tmp_path).lab_folder(*labs)
    (label, _), = card.form(_account(PERMISSION_GROUPS + labs, primary="zeta.grp"))["options"]
    assert label == (f"{REFUSAL}: ask {CONTACT} to enrol one of "
                     "zeta.grp, alpha.grp, beta.grp (+2 more)"), label


@needs_ruby
def test_no_group_with_a_lab_folder_names_no_group_at_all(tmp_path):
    """Degenerate: nothing on disk is a lab. Guessing (the primary group, the
    first groups) is how six permission groups got named; say so instead."""
    card = Card(tmp_path)                                  # no lab folders at all
    acct = _account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp")
    (label, value), = card.form(acct)["options"]
    assert value == "" and label.startswith(REFUSAL), label
    assert "lab folder" in label and CONTACT in label, label
    assert not [g for g in PERMISSION_GROUPS + ["zeta.grp"] if g in label], label


@needs_ruby
def test_an_enrolled_lab_is_offered_by_value_and_the_field_is_required(tmp_path):
    card = Card(tmp_path).lab_folder("zeta.grp", enrolled=True).lab_folder("beta.grp")
    lab = card.form(_account(PERMISSION_GROUPS + ["beta.grp", "zeta.grp"], primary="zeta.grp"))
    assert lab["options"] == [["zeta.grp  ✓", "zeta.grp"]]
    assert lab.get("required") is True


@needs_ruby
def test_a_deployment_without_labs_neither_refuses_nor_requires(tmp_path):
    """The other side. Group scope off: an empty lab is the ordinary launch. The
    first no-enrolled fix rendered 'not enrolled' + `required` here too, which —
    wherever a browser enforced it — blocked EVERY launch on such a deployment."""
    card = Card(tmp_path, labs_enabled=False).lab_folder("zeta.grp")
    lab = card.form(_account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp"))
    (label, value), = lab["options"]
    assert value == "" and not label.startswith(REFUSAL), label
    assert "required" not in lab


_ENROLLED_TEXT = "Your enrolled ABA groups (✓)"


@needs_ruby
@pytest.mark.parametrize("case", ["enrolled", "not-enrolled", "no-labs"])
def test_the_Lab_help_says_what_the_form_can_do_and_is_RED_only_when_it_cannot(tmp_path, case):
    """The help under Lab was one site string for every case, so a user with no
    enrolled lab read "Your enrolled ABA groups (✓)" beneath a field holding
    none. It follows the case now, and only the case that cannot launch is red."""
    card = Card(tmp_path, labs_enabled=(case != "no-labs"))
    card.lab_folder("zeta.grp", enrolled=(case == "enrolled"))
    help_ = card.form(_account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp"))["help"]
    red = "text-danger" in help_
    if case == "not-enrolled":
        assert red and "cannot start" in help_, help_
    else:
        assert not red, f"{case}: a form that can launch is shown in red: {help_}"
    assert (_ENROLLED_TEXT in help_) == (case == "enrolled"), (
        f"{case}: the enrolled wording belongs exactly where enrolled labs are offered: {help_}")


@needs_ruby
def test_a_site_words_both_cases_and_the_card_keeps_the_red(tmp_path):
    ui = {"form_intro": "Pick your lab.", "form_not_enrolled": "No lab yet — email the desk."}
    acct = _account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp")
    offered = Card(tmp_path / "a", ui=ui).lab_folder("zeta.grp", enrolled=True).form(acct)
    assert offered["help"] == "Pick your lab."
    refused = Card(tmp_path / "b", ui=ui).lab_folder("zeta.grp").form(acct)
    assert "No lab yet — email the desk." in refused["help"], refused["help"]
    assert "text-danger" in refused["help"], refused["help"]


@needs_ruby
@pytest.mark.parametrize("enrolled", [True, False], ids=["enrolled", "not-enrolled"])
def test_a_quote_in_the_sites_wording_cannot_break_the_form(tmp_path, enrolled):
    """Every site string used to be spliced into a YAML "…" scalar, so one `"`
    in the wording ended it early: the template rendered, the YAML did not
    parse, and OnDemand showed an error page where the form should be.
    Degenerate input, ordinary wording — `Click "Request access"`."""
    ui = {"enroll_contact": 'the "lab" desk', "form_intro": 'Click "Request access".',
          "form_not_enrolled": 'Use "Request access".'}
    form = {"instances": [{"id": "s", "label": 'Small "S"', "cores": 2, "mem": "8G"}],
            "walltimes": [{"label": '"Short" 1h', "seconds": 3600}]}
    card = Card(tmp_path, ui=ui, form=form).lab_folder("zeta.grp", enrolled=enrolled)
    attrs = card.rendered(_account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp"))["attributes"]
    assert attrs["aba_instance"]["options"] == [['Small "S" — 2 cores, 8G RAM', "s"]]
    assert attrs["aba_walltime"]["options"] == [['"Short" 1h', "3600"]]
    lab = attrs["aba_lab"]
    if enrolled:
        assert lab["help"] == 'Click "Request access".'
    else:
        assert 'Use "Request access".' in lab["help"]
        assert 'the "lab" desk' in lab["options"][0][0], lab["options"]


# ── submit.yml.erb: the refusal that holds whatever the browser does ─────────

_SIZE = {"aba_instance": "light", "aba_walltime": "14400"}


@needs_ruby
@pytest.mark.parametrize("lab", [{"aba_lab": ""}, {"aba_lab": "   "}, {}],
                         ids=["empty", "blank", "absent"])
def test_submit_REFUSES_a_launch_with_no_lab_where_labs_are_required(tmp_path, lab):
    """The forbidden action is a submission, so that is what is asserted: the
    template raises (OnDemand shows it on the form and submits nothing) and
    renders NO job at all — not merely a job carrying a warning."""
    r = Card(tmp_path).submit({**_SIZE, **lab})
    assert r.returncode == 1, f"rc={r.returncode}: a launch with no lab was not refused\n{r.stdout}"
    assert not r.stdout.strip(), "a refused launch still rendered a submission"
    assert "enrolled lab" in r.stderr and CONTACT in r.stderr, r.stderr


@needs_ruby
def test_submit_renders_the_job_for_an_enrolled_lab(tmp_path):
    """ARMED: the same render with a lab must produce the job, or the refusal
    above would pass on a template that refuses everything."""
    r = Card(tmp_path).submit({**_SIZE, "aba_lab": "zeta.grp"})
    assert r.returncode == 0, r.stderr
    native = json.loads(r.stdout)["script"]["native"]
    assert native[:4] == ["-N", "1", "-c", "4"], native


@needs_ruby
def test_submit_does_not_refuse_where_the_deployment_uses_no_labs(tmp_path):
    r = Card(tmp_path, labs_enabled=False).submit({**_SIZE, "aba_lab": ""})
    assert r.returncode == 0, r.stderr


# ── form.js: Launch greyed out on the form the card ACTUALLY renders ─────────

_DOM = r"""
const spec = JSON.parse(process.env.ABA_DOM_SPEC);
const { Window } = await import(spec.happydom);
const fs = await import('node:fs');
const vm = await import('node:vm');
const w = new Window();
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const select = spec.options === null ? '' :
  '<select id="batch_connect_session_context_aba_lab" name="batch_connect_session_context[aba_lab]">' +
  spec.options.map(([l, v]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('') +
  '</select>';
w.document.body.innerHTML = '<form id="new_batch_connect_session_context">' + select +
  '<input type="submit" name="commit" value="Launch"></form>';
vm.runInContext(fs.readFileSync(spec.formjs, 'utf8'),
                vm.createContext({ document: w.document, window: w }));
const b = w.document.querySelector('[type=submit]');
console.log(JSON.stringify({ disabled: b.disabled, title: b.getAttribute('title') || '' }));
await w.happyDOM.close();
"""


def _launch(options) -> dict:
    """Run form.js over OnDemand's form markup holding `options`; report Launch."""
    spec = {"happydom": HAPPY_DOM.as_uri(), "formjs": str(APP / "form.js"), "options": options}
    r = subprocess.run([NODE, "--input-type=module", "-e", _DOM], capture_output=True,
                       text=True, env={**os.environ, "ABA_DOM_SPEC": json.dumps(spec)},
                       timeout=60)
    assert r.returncode == 0, f"form.js did not run:\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


@needs_ruby
@needs_dom
def test_Launch_is_greyed_out_on_the_form_rendered_for_an_unenrolled_user(tmp_path):
    """The options come from the REAL render, not a literal: this is where the
    two files meet, so a label the form stops producing fails here."""
    card = Card(tmp_path).lab_folder("zeta.grp")
    opts = card.form(_account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp"))["options"]
    launch = _launch(opts)
    assert launch["disabled"] is True, "Launch is live on a form that cannot launch"
    assert "zeta.grp" in launch["title"], launch


@needs_ruby
@needs_dom
@pytest.mark.parametrize("labs_enabled", [True, False], ids=["enrolled-lab", "no-labs-deployment"])
def test_Launch_stays_live_wherever_a_launch_can_succeed(tmp_path, labs_enabled):
    """The other side, including the degenerate one: an EMPTY value that is not
    a refusal (a deployment without labs) must leave Launch alone."""
    card = Card(tmp_path, labs_enabled=labs_enabled).lab_folder("zeta.grp", enrolled=True)
    opts = card.form(_account(PERMISSION_GROUPS + ["zeta.grp"], primary="zeta.grp"))["options"]
    assert _launch(opts)["disabled"] is False


@needs_dom
def test_formjs_leaves_a_form_without_a_Lab_field_alone():
    assert _launch(None) == {"disabled": False, "title": ""}
