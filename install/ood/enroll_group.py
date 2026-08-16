#!/usr/bin/env python3
"""enroll-group — enroll a lab GROUP in ABA (the pilot gate).

Usage:
  enroll_group.py <group> [--site PATH] [--yes] [--dry-run] [--validate-only]
        [--paste-token | --api-key sk-ant-api… | --oauth-token sk-ant-oat…
         | --cred-file FILE]
        [--by NAME]

WHO RUNS THIS: one person per lab, once, and quite possibly someone who has
never used a terminal before. That audience is the whole design brief:

  * Nothing is created until the operator has SEEN what will happen and agreed.
    The plan is resolved and checked first, printed in full, then confirmed.
  * Every refusal is a plain sentence plus a next step — never a traceback.
    A Python stack trace tells this operator nothing and reads as breakage.
  * The checks that matter run BEFORE any mutation, not after. A mistyped group
    name used to create /groups/<typo>/aba and then print "✓ enrolled", so the
    lab silently never appeared; the unix group is now verified up front and a
    typo refuses without touching the disk.
  * What was done is verified independently afterwards, by re-reading the disk
    and asking the question the LAUNCH FORM asks — not by trusting the writes
    that just happened.

Reads the SAME site.yaml that aba-preflight + the launch form read, then:
  1. creates <root_path> from the skeleton (drops the .aba-workspace stamp) —
     the signal that makes the group appear on the form and pass preflight.
     Idempotent; REFUSES a foreign same-named folder.
  2. records the enrollment (date / by / credential mode) in .aba-workspace,
     PRESERVING the original enrolment date when re-run to rotate a credential.
  3. (optional) writes the lab-shared credential at credentials.group_key_path
     (mode 0640 — owner + LAB, never world; 0600 made the shared credential
     unreadable by the very group it is shared with) — an Anthropic API key, an
     OAuth token, or a ready cred file.
  4. makes the workspace group-owned + setgid, so the lab shares it.
  5. validates the result and says plainly whether the lab is now enrolled.

Exit codes:  0 = done (or dry-run/validation OK)
             2 = refused before changing anything
             3 = changes were made but validation failed
"""
import argparse
import datetime
import getpass
import grp
import json
import os
import shutil
import stat
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("enroll-group: this needs the PyYAML package, which appears to be "
             "missing.\nWhat to do: ask your cluster admin for a python3 with "
             "PyYAML, or run this on the login node where it is installed.")

OURS_MARKERS = (".aba-workspace", ".bundle", ".envs")   # == aba_preflight.OURS_MARKERS

# Credential kinds we know how to write, and the prefix each one really has.
# The prefix is CHECKED, not assumed: an `sk-ant-api…` handed to --oauth-token
# used to be written as claude_code_oauth_token without complaint, and a
# credential in the wrong slot resolves to a weaker auth mode — the lab quietly
# loses the model tier it thought it was getting. Wrong slot is a refusal.
CRED_KINDS = {
    "oauth-token": {"json_key": "claude_code_oauth_token", "prefix": "sk-ant-oat"},
    "api-key":     {"json_key": "anthropic_api_key",       "prefix": "sk-ant-api"},
}


class Refusal(Exception):
    """A problem the operator can act on. Carries the fix, not just the fault.

    Everything raised at the operator is this: the message is two lines, what
    is wrong and what to do about it. A bare exception type reaching the
    terminal is a bug in this script, not information for the reader."""

    def __init__(self, what, fix):
        super().__init__(what)
        self.what = what
        self.fix = fix


class Plan:
    """Everything resolved and checked, before anything is written.

    Building this object must not touch the disk beyond reading. It exists so
    the operator can be shown exactly what will happen — the same values that
    the apply step will then use, rather than a prose approximation of them."""

    def __init__(self, group, group_dir, root, skeleton, cred_kind, cred_path,
                 cred_data, site_path, by):
        self.group = group              # unix group name, e.g. tanaka.grp
        self.group_dir = group_dir      # on-disk folder name, e.g. tanaka
        self.root = root                # the ABA workspace path
        self.skeleton = skeleton
        self.cred_kind = cred_kind      # None | "oauth-token" | "api-key" | "file"
        self.cred_path = cred_path
        self.cred_data = cred_data      # already-validated JSON text, or None
        self.site_path = site_path
        self.by = by
        self.already = False            # workspace already exists and is ours


def _read_site(path):
    p = Path(path)
    if not p.exists():
        raise Refusal(
            f"I could not find the ABA site configuration at {p}.",
            "Check the path, or pass --site /path/to/site.yaml. Your cluster "
            "admin can tell you where ABA is installed.")
    try:
        return yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise Refusal(f"The site configuration at {p} is not valid YAML ({e.__class__.__name__}).",
                      "Ask your cluster admin to check that file — this is not "
                      "something you can fix from here.")
    except OSError as e:
        raise Refusal(f"I could not read {p}: {e.strerror}.",
                      "Check that you have permission to read it, then try again.")


def _check_group_exists(group):
    """The check that has to happen FIRST.

    A mistyped group name is the single most likely operator error, and it used
    to be discovered only at the very end (as a printed note, after the folder
    had been created and '✓ enrolled' promised). Verified here, a typo costs
    nothing and explains itself."""
    try:
        return grp.getgrnam(group)
    except KeyError:
        raise Refusal(
            f"There is no unix group called {group!r} on this cluster.",
            "Check the spelling. To see the groups you belong to, run:  groups")


# Shortest body we will believe after the prefix. Real tokens run ~100 chars;
# this only has to be long enough that a truncated copy cannot pass.
_CRED_MIN_BODY = 20

PASTE_HELP = """
To get a token, open a SECOND terminal window and run:

    claude setup-token

It asks you to sign in with a browser, then prints one long line starting
with sk-ant-oat01- . Copy that whole line and paste it below.
"""


def _token_as_credential(value):
    """(kind, json_text) for a token typed or pasted by a person.

    Which slot it belongs in is DETECTED from the prefix rather than asked,
    because the operator has no way to know the difference and a credential in
    the wrong slot resolves to a weaker auth mode — the lab quietly loses the
    model tier it thought it was getting."""
    value = value.strip().strip('"').strip("'").strip()
    if not value:
        raise Refusal(
            "Nothing was pasted, so there is no credential to install.",
            "Run `claude setup-token`, copy the sk-ant-oat01-… line it prints, "
            "then run this command again.")
    for flag, spec in CRED_KINDS.items():
        if value.startswith(spec["prefix"]):
            # A half-copied line still starts with the prefix. Written out, it
            # enrols cleanly and then fails at somebody's first launch, where
            # nothing points back to this moment.
            if len(value) - len(spec["prefix"]) < _CRED_MIN_BODY:
                raise Refusal(
                    "That looks like the beginning of a token, but it was cut short.",
                    "Select the WHOLE line — real tokens are around a hundred "
                    "characters long — and paste it again.")
            return flag, json.dumps({spec["json_key"]: value}) + "\n"
    raise Refusal(
        "That does not look like a Claude token or an Anthropic API key.",
        "It should begin with sk-ant-oat01- (from `claude setup-token`) or "
        "sk-ant-api- (an API key). Copy the whole line, with nothing in front "
        "of it — a copied shell prompt or a stray quote is the usual cause.")


def _prompt_for_token():
    """Ask for the token instead of taking it on the command line.

    `--oauth-token <secret>` works, but it leaves the secret in the shell
    history and, while it runs, in `ps` for everyone on the login node. The
    operator this script is written for cannot be expected to know that, so the
    safe way has to be the easy way."""
    if not sys.stdin.isatty():
        raise Refusal(
            "--paste-token needs a terminal it can ask a question on.",
            "Run this command directly in a terminal window, or supply the "
            "credential with --cred-file instead.")
    print(PASTE_HELP)
    try:
        value = getpass.getpass("Paste the token here (it will NOT appear on screen): ")
    except (EOFError, KeyboardInterrupt):
        raise Refusal("Nothing was pasted, so there is no credential to install.",
                      "Run this command again when you have a token.")
    kind, text = _token_as_credential(value)
    # Say enough that the operator knows the paste landed, and no more. A hidden
    # prompt gives no feedback at all, which reads as "it didn't take".
    print(f"· read a {kind} of {len(value.strip())} characters\n")
    return kind, text


def _validated_credential(a):
    """Return (kind, json_text) for the credential flags, or (None, None).

    Refuses a credential in the wrong slot and a --cred-file that is not the
    JSON this script writes; a 0600 file full of the wrong thing fails later,
    at a user's first launch, where nobody can connect it back to enrolment."""
    if getattr(a, "paste_token", False):
        return _prompt_for_token()

    if a.cred_file:
        p = Path(a.cred_file)
        if not p.exists():
            raise Refusal(f"I could not find the credential file {p}.",
                          "Check the path and try again.")
        try:
            text = p.read_text()
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise Refusal(f"The credential file {p} is not valid JSON.",
                          'It should contain one line like: '
                          '{"claude_code_oauth_token": "sk-ant-oat01-…"}')
        except OSError as e:
            raise Refusal(f"I could not read {p}: {e.strerror}.",
                          "Check that you have permission to read it.")
        if not isinstance(parsed, dict):
            raise Refusal(f"The credential file {p} must contain a JSON object.",
                          'For example: {"claude_code_oauth_token": "sk-ant-oat01-…"}')
        known = {k["json_key"] for k in CRED_KINDS.values()}
        if not (set(parsed) & known):
            raise Refusal(
                f"The credential file {p} has none of the keys ABA understands "
                f"({', '.join(sorted(known))}).",
                'Rewrite it as: {"claude_code_oauth_token": "sk-ant-oat01-…"}')
        return "file", text

    for flag, spec in CRED_KINDS.items():
        value = getattr(a, flag.replace("-", "_"))
        if not value:
            continue
        if not value.startswith(spec["prefix"]):
            other = next((k for k, s in CRED_KINDS.items()
                          if value.startswith(s["prefix"])), None)
            hint = (f"That value looks like a {other} — pass it as --{other} instead."
                    if other else
                    f"A {flag} should start with {spec['prefix']}…")
            raise Refusal(f"The value given to --{flag} does not look like a {flag}.", hint)
        return flag, json.dumps({spec["json_key"]: value}) + "\n"

    return None, None


def build_plan(a):
    """Resolve + check everything. Raises Refusal; never writes."""
    site = _read_site(a.site)
    gcfg = (site.get("scopes") or {}).get("group") or {}

    # On-disk FOLDER name vs the unix GROUP name: some sites suffix the unix
    # group while the shared folder omits it. {group_dir} = folder, {group} =
    # unix name (used for ownership). No-op when strip_suffix is unset.
    strip = str(gcfg.get("strip_suffix") or "")
    group_dir = a.group[:-len(strip)] if (strip and a.group.endswith(strip)) else a.group

    def _ex(s):
        out = s.replace("{group_dir}", group_dir).replace("{group}", a.group)
        # aba_preflight's expander (aba_preflight.py:216) also knows {user} and
        # {home}. This tool deliberately does NOT copy that vocabulary — a
        # second, half-complete copy is how the writer and the reader drift.
        # But an unexpanded placeholder must never reach the filesystem: it
        # would create a directory literally named "{user}" here while preflight
        # looked somewhere else, and the lab would never appear with nothing to
        # explain why. Refuse instead.
        if "{" in out:
            raise Refusal(
                f"This ABA installation's settings use a placeholder this tool "
                f"does not understand: {s}",
                "Enrolment cannot safely guess where the folder should go. "
                "Please send this line to your ABA admin.")
        return out

    root = Path(_ex(gcfg.get("root_path") or "/groups/{group}/aba"))
    skeleton = gcfg.get("skeleton_template")
    cred_kind, cred_data = _validated_credential(a)

    cred_path = None
    if cred_kind:
        gkey = (site.get("credentials") or {}).get("group_key_path")
        if not gkey:
            raise Refusal(
                "This ABA installation has no place configured for a lab-shared "
                "credential (credentials.group_key_path is not set).",
                "Re-run without the credential option to enrol the group anyway "
                "— each user can then connect their own subscription in "
                "Settings → Agent. Or ask your admin to set that key.")
        cred_path = Path(_ex(gkey))

    plan = Plan(a.group, group_dir, root, skeleton, cred_kind, cred_path,
                cred_data, a.site, a.by)

    _check_group_exists(a.group)          # FIRST, and fatal

    # Target state: ours (re-run), foreign (refuse), or new (needs a writable parent).
    if root.exists() and any((root / m).exists() for m in OURS_MARKERS):
        plan.already = True
    elif root.exists() and any(root.iterdir()):
        raise Refusal(
            f"{root} already exists and is not an ABA workspace.",
            "Something else is using that folder. Move it aside (or ask your "
            "admin to), then run this again.")
    else:
        parent = root.parent
        if not parent.exists():
            raise Refusal(
                f"The folder {parent} does not exist, so I cannot create {root}.",
                "This usually means the lab has no shared directory yet. Ask "
                "your cluster admin to create it.")
        if not os.access(parent, os.W_OK):
            raise Refusal(
                f"You do not have permission to create {root}.",
                f"Ask someone who can write to {parent} to run this command, or "
                "ask your cluster admin for access.")

    if skeleton and not Path(skeleton).is_dir():
        raise Refusal(
            f"The ABA starter template is missing: {skeleton}.",
            "This is an installation problem, not something you did. Please "
            "send this message to your ABA admin.")

    return plan


def render_plan(plan):
    """What the operator sees before saying yes. Resolved values only."""
    L = [""]
    L.append(f"  Lab group        {plan.group}   (unix group; exists ✓)")
    if plan.group_dir != plan.group:
        L.append(f"  Lab folder name  {plan.group_dir}")
    L.append(f"  ABA workspace    {plan.root}")
    L.append("                   " + ("already set up — will be left in place, "
                                       "not overwritten" if plan.already
                                       else "will be CREATED now"))
    if plan.cred_kind:
        # Spelled out, because "a oauth-token" is what a flag name reads like,
        # not what a person reads.
        said = {"oauth-token": "a Claude OAuth token, shared by the whole lab",
                "api-key": "an Anthropic API key, shared by the whole lab",
                "file": "the login from the file you gave, shared by the whole lab"}
        L.append(f"  Shared login     {said.get(plan.cred_kind, plan.cred_kind)}")
        L.append(f"                   will be written to {plan.cred_path}")
        L.append("                   (readable only by you and the lab; not shown on screen)")
    else:
        L.append("  Shared login     none — each person connects their own in "
                 "Settings → Agent")
    L.append(f"  Owned by group   {plan.group}, shared (setgid)")
    L.append(f"  Using settings   {plan.site_path}")
    L.append("")
    return "\n".join(L)


def confirm(assume_yes):
    """Ask. Refuse rather than guess when nobody can answer."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise Refusal(
            "I need someone to confirm this, but there is nobody at the keyboard.",
            "Run this again with --yes if you are sure, or run it in a normal "
            "terminal where you can answer.")
    try:
        answer = input("Go ahead and do this? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("y", "yes")


def apply_plan(plan):
    """Do it. Anything that goes wrong here is reported honestly."""
    if plan.already:
        print(f"· workspace already present: {plan.root}")
    else:
        plan.root.mkdir(parents=True, exist_ok=True)
        if plan.skeleton:
            shutil.copytree(plan.skeleton, plan.root, dirs_exist_ok=True)
        else:
            (plan.root / ".aba-workspace").touch()
        print(f"· created workspace: {plan.root}")

    # Enrolment record. Re-running to rotate a credential must NOT rewrite the
    # original enrolment date — that is the one fact this file exists to keep.
    stamp = plan.root / ".aba-workspace"
    first_at, first_by = None, None
    if stamp.exists():
        for line in stamp.read_text().splitlines():
            if line.startswith("enrolled_at:") and first_at is None:
                first_at = line.split(":", 1)[1].strip()
            elif line.startswith("enrolled_by:") and first_by is None:
                first_by = line.split(":", 1)[1].strip()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    cred_mode = plan.cred_kind or "none (each user connects their own)"
    body = ["# This folder is an ABA workspace (marker read by aba-preflight).",
            f"enrolled_at: {first_at or now}",
            f"enrolled_by: {first_by or plan.by}",
            f"credential: {cred_mode}"]
    if first_at:                                    # a re-run: record it as such
        body.append(f"updated_at: {now}")
        body.append(f"updated_by: {plan.by}")
    stamp.write_text("\n".join(body) + "\n")
    print(f"· recorded enrolment in {stamp.name}")

    if plan.cred_kind:
        # 0640 and group-owned, NOT 0600. This credential exists so the whole
        # lab launches without an auth prompt, and aba_preflight reads it AS THE
        # LAUNCHING USER — so at 0600 it is readable by exactly one person: the
        # one who ran this script. Worse, read_cred_file() swallows every
        # exception, so PermissionError is indistinguishable from "no file":
        # every other member silently falls through to no credential, while the
        # person who enrolled the lab tests it and sees it work.
        # World bits stay off — this is a secret shared with the lab, not the
        # cluster.
        plan.cred_path.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o027)
        try:
            plan.cred_path.write_text(plan.cred_data)
        finally:
            os.umask(old)
        try:
            os.chown(plan.cred_path, -1, grp.getgrnam(plan.group).gr_gid)
        except (PermissionError, KeyError):
            pass                       # validate() decides whether this matters
        os.chmod(plan.cred_path, 0o640)
        print(f"· wrote the lab's shared login to {plan.cred_path}")
        # Say what the filesystem actually did, not what we asked it to do.
        print(f"  readable by: {_effective_readers(plan.cred_path, plan.group)}")

    gid = grp.getgrnam(plan.group).gr_gid        # existence proven in preflight
    try:
        os.chown(plan.root, -1, gid)
        os.chmod(plan.root, 0o2775)
        # Report the OUTCOME. `chmod 2775` is a silent no-op on the lab export
        # (NFSv4, ACL-enforced), so "shared the workspace" was a claim about a
        # syscall's return value, not about the folder — and validate() then
        # contradicted it two lines later.
        ok, why = _new_files_belong_to_the_lab(plan.root, gid)
        if ok:
            print(f"· shared the workspace with the {plan.group} group")
        else:
            print(f"· note: {plan.root} may not be shared with {plan.group} ({why})")
    except PermissionError:
        # Not fatal, but NOT a success either — validation decides.
        print(f"· note: could not hand {plan.root} to the {plan.group} group "
              f"(you may not be a member)")


def _new_files_belong_to_the_lab(root: Path, want_gid: int):
    """(True|False|None, explanation) — make a real file and see who owns it.

    The only test that survives a filesystem with its own ideas about
    permissions. Cleans up after itself even when the check fails."""
    probe = root / f".aba-enrol-check-{os.getpid()}"
    try:
        probe.write_text("")
    except OSError as e:
        return None, f"cannot write there: {e}"
    try:
        got = probe.stat().st_gid
        if got == want_gid:
            return True, ""
        try:
            got_name = grp.getgrgid(got).gr_name
        except KeyError:
            got_name = f"gid {got}"
        return False, f"it came out owned by {got_name}"
    except OSError as e:
        return None, str(e)
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _effective_readers(path: Path, group: str) -> str:
    """Who can actually read this file, in plain words.

    Says what the FILESYSTEM reports, not what we asked for. On the lab export
    `chmod 0640` is silently discarded and every mode reads 0777 — access is
    enforced by an ACL the mode bits do not describe — so quoting our own
    intent back at the operator would be a guess dressed as a fact."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as e:
        return f"could not be checked ({e})"
    if mode == 0o640:
        return f"you and the {group} group"
    if mode & 0o007:
        return (f"reported as mode {mode:04o} — this filesystem does not appear "
                f"to honour permission bits (it kept {mode:04o} after we asked "
                f"for 0640), so access is whatever its own ACLs allow. On the "
                f"lab shares here that is the lab; confirm with your admin if "
                f"the credential is sensitive")
    return f"reported as mode {mode:04o}"


def validate(plan):
    """Re-read from disk and ask the question the LAUNCH FORM asks.

    Deliberately independent of what apply_plan believes it wrote: the point is
    to catch the case where the writes 'succeeded' and the lab still will not
    appear. Returns a list of problems; empty means enrolled for real."""
    problems = []
    if not plan.root.is_dir():
        return [f"{plan.root} does not exist."]

    # (a) the form's own predicate: at least one marker, and readable.
    if not any((plan.root / m).exists() for m in OURS_MARKERS):
        problems.append(
            f"{plan.root} has none of the markers ABA looks for "
            f"({', '.join(OURS_MARKERS)}) — the lab will not appear on the form.")
    if not os.access(plan.root, os.R_OK | os.X_OK):
        problems.append(f"{plan.root} is not readable, so ABA cannot see it.")

    # (b) shared with the lab. Ask the OUTCOME ("does a file made here belong to
    # the lab?"), not the mechanism ("is the setgid bit set?").
    #
    # setgid is one way to get that outcome, and on a local filesystem it is the
    # way. The lab trees here are an NFSv4 export that enforces access by ACL and
    # ignores chmod entirely: `chmod 2775` returns success, the bit never appears,
    # and every mode reads 0777 no matter what anyone writes. Yet new files DO
    # inherit the lab group, because the server does the inheriting. Checking the
    # bit therefore fails every enrolment on the only filesystem this pilot runs
    # on, and hands the operator a remedy no admin can perform. Checking the
    # outcome passes there and still catches the real breakage on a local disk.
    try:
        st = plan.root.stat()
        want = grp.getgrnam(plan.group).gr_gid
        if st.st_gid != want:
            problems.append(
                f"{plan.root} is not owned by the {plan.group} group, so other "
                f"lab members will not be able to use it.")
        shared, why = _new_files_belong_to_the_lab(plan.root, want)
        if shared is False:
            problems.append(
                f"a file created in {plan.root} does not belong to the "
                f"{plan.group} group ({why}), so work by one member will not be "
                f"shared with the rest of the lab.")
        elif shared is None:
            problems.append(f"could not check sharing in {plan.root}: {why}")
    except (KeyError, OSError) as e:
        problems.append(f"could not check ownership of {plan.root}: {e}")

    # (c) the credential, if one was placed: parses, has a known key, is private.
    if plan.cred_kind:
        if not plan.cred_path.exists():
            problems.append(f"the shared login file {plan.cred_path} is missing.")
        else:
            try:
                parsed = json.loads(plan.cred_path.read_text())
                known = {k["json_key"] for k in CRED_KINDS.values()}
                if not (set(parsed) & known):
                    problems.append(
                        f"{plan.cred_path} does not contain a login ABA understands.")
            except (json.JSONDecodeError, OSError):
                problems.append(f"{plan.cred_path} could not be read back as JSON.")
            # Two-sided: the LAB must be able to read it (or only the enroller
            # can launch), and the rest of the cluster must not. Checking only
            # one side is how 0600 survived — "not world-readable" looked right.
            if plan.cred_path.exists():
                st = plan.cred_path.stat()
                if st.st_mode & 0o007:
                    problems.append(
                        f"{plan.cred_path} is readable by anyone on the cluster "
                        f"— it must be limited to the lab.")
                if not st.st_mode & 0o040:
                    problems.append(
                        f"{plan.cred_path} is not readable by the {plan.group} "
                        f"group, so only you would be able to start a session — "
                        f"everyone else would silently get no login.")
                try:
                    if st.st_gid != grp.getgrnam(plan.group).gr_gid:
                        problems.append(
                            f"{plan.cred_path} does not belong to the "
                            f"{plan.group} group, so the lab cannot read it.")
                except KeyError:
                    pass
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="enroll-group", description="Enrol a lab group in ABA.")
    ap.add_argument("group", help="the lab's unix group name")
    ap.add_argument("--site", default=os.environ.get("ABA_SITE_CONFIG",
                                                     "/cluster/aba/site.yaml"),
                    help="site.yaml (default: $ABA_SITE_CONFIG)")
    ap.add_argument("--by", default=getpass.getuser(), help="who is enrolling (for the record)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation question")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen and stop, changing nothing")
    ap.add_argument("--validate-only", action="store_true",
                    help="check an existing enrolment; change nothing")
    ap.add_argument("--debug", action="store_true",
                    help="show the full Python error if something breaks")
    c = ap.add_mutually_exclusive_group()
    c.add_argument("--api-key", help="Anthropic API key (sk-ant-api…) — lab-shared")
    c.add_argument("--oauth-token", help="Claude OAuth token (sk-ant-oat…) — lab-shared")
    c.add_argument("--cred-file", help="path to a ready credentials.json to install")
    c.add_argument("--paste-token", action="store_true",
                   help="ask for the token and read it without echoing — keeps it "
                        "out of the shell history and out of `ps`")
    a = ap.parse_args(argv)

    try:
        plan = build_plan(a)

        if a.validate_only:
            problems = validate(plan)
            if problems:
                print(f"✗ {plan.group} is NOT correctly enrolled:")
                for p in problems:
                    print(f"    · {p}")
                return 3
            print(f"✓ {plan.group} is enrolled and looks correct.")
            return 0

        print("\nHere is what I found, and what I am about to do:")
        print(render_plan(plan))
        if a.dry_run:
            print("Nothing was changed (--dry-run).")
            return 0
        if not confirm(a.yes):
            print("Stopped. Nothing was changed.")
            return 0

        print()
        apply_plan(plan)

        problems = validate(plan)
        if problems:
            print(f"\n✗ Something is not right — {plan.group} may not work yet:")
            for p in problems:
                print(f"    · {p}")
            print("\nWhat to do: send the lines above to your ABA admin.")
            return 3
        print(f"\n✓ Done. '{plan.group}' is enrolled — it will now appear on the "
              f"ABA launch form for members of that group.")
        return 0

    except Refusal as r:
        print(f"\nProblem: {r.what}\nWhat to do: {r.fix}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Nothing was changed.", file=sys.stderr)
        return 2
    except Exception as e:                       # never show a traceback
        if a.debug:
            raise
        print(f"\nProblem: something went wrong that I did not expect "
              f"({e.__class__.__name__}: {e}).\n"
              f"What to do: send this message to your ABA admin. Re-running with "
              f"--debug will show the full technical details.\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
