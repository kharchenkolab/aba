#!/usr/bin/env python3
"""enroll-group — enroll a lab GROUP in ABA (admin action; the pilot gate).

Usage:
  enroll_group.py <group> [--site PATH]
        [--api-key sk-ant-api… | --oauth-token sk-ant-oat… | --cred-file FILE]
        [--by NAME]

Reads the SAME site.yaml that aba-preflight + the launch form read, then:
  1. creates /groups/<group>/aba from the skeleton (drops the .aba-workspace
     stamp) — the signal that makes the group appear on the form and pass
     preflight. Idempotent; REFUSES a foreign same-named folder.
  2. records the enrollment (date / by / credential mode) in .aba-workspace.
  3. (optional) writes the lab-shared credential at credentials.group_key_path
     (mode 0600) — an Anthropic API key, an OAuth token, or a ready cred file.
  4. makes the workspace group-owned + setgid (best-effort), so the lab shares it.

After this the group is enrolled. Leave the credential out to have each user
paste one on the launch form.
"""
import argparse
import datetime
import getpass
import grp
import json
import os
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("enroll-group: PyYAML required")

OURS_MARKERS = (".aba-workspace", ".bundle", ".envs")   # == aba_preflight.OURS_MARKERS


def main(argv=None):
    ap = argparse.ArgumentParser(prog="enroll-group", description="Enroll a lab group in ABA.")
    ap.add_argument("group", help="the lab's unix group name")
    ap.add_argument("--site", default=os.environ.get("ABA_SITE_CONFIG", "/cluster/aba/site.yaml"),
                    help="site.yaml (default: $ABA_SITE_CONFIG or /cluster/aba/site.yaml)")
    ap.add_argument("--by", default=getpass.getuser(), help="who is enrolling (for the record)")
    c = ap.add_mutually_exclusive_group()
    c.add_argument("--api-key", help="Anthropic API key (sk-ant-api…) — lab-shared")
    c.add_argument("--oauth-token", help="Claude OAuth token (sk-ant-oat…) — lab-shared")
    c.add_argument("--cred-file", help="path to a ready credentials.json to install")
    a = ap.parse_args(argv)

    site = yaml.safe_load(Path(a.site).read_text()) or {}
    gcfg = (site.get("scopes") or {}).get("group") or {}
    group_root = Path((gcfg.get("root_path") or "/groups/{group}/aba").replace("{group}", a.group))
    tmpl = gcfg.get("skeleton_template")

    # 1) workspace
    if group_root.exists() and any((group_root / m).exists() for m in OURS_MARKERS):
        print(f"already enrolled: {group_root}")
    elif group_root.exists() and any(group_root.iterdir()):
        sys.exit(f"REFUSING: {group_root} exists and is NOT an ABA workspace "
                 f"(no {'/'.join(OURS_MARKERS)} marker). Move it aside, then re-run.")
    else:
        group_root.mkdir(parents=True, exist_ok=True)
        if tmpl and Path(tmpl).is_dir():
            shutil.copytree(tmpl, group_root, dirs_exist_ok=True)
        else:
            (group_root / ".aba-workspace").touch()
        print(f"created ABA workspace: {group_root}")

    # 2) enrollment record (the .aba-workspace stamp doubles as a record)
    cred_mode = ("api-key" if a.api_key else "oauth" if a.oauth_token
                 else "file" if a.cred_file else "none (per-user form paste)")
    (group_root / ".aba-workspace").write_text(
        "# This folder is an ABA workspace (marker read by aba-preflight).\n"
        f"enrolled_at: {datetime.datetime.now().isoformat(timespec='seconds')}\n"
        f"enrolled_by: {a.by}\n"
        f"credential: {cred_mode}\n")

    # 3) lab-shared credential (optional, 0600)
    if a.api_key or a.oauth_token or a.cred_file:
        gkey = (site.get("credentials") or {}).get("group_key_path")
        if not gkey:
            sys.exit("credentials.group_key_path not set in site.yaml — cannot place a lab key.")
        gkey_path = Path(gkey.replace("{group}", a.group))
        gkey_path.parent.mkdir(parents=True, exist_ok=True)
        if a.cred_file:
            data = Path(a.cred_file).read_text()
        elif a.api_key:
            data = json.dumps({"anthropic_api_key": a.api_key}) + "\n"
        else:
            data = json.dumps({"claude_code_oauth_token": a.oauth_token}) + "\n"
        old = os.umask(0o077)
        try:
            gkey_path.write_text(data)
        finally:
            os.umask(old)
        os.chmod(gkey_path, 0o600)
        print(f"wrote lab credential: {gkey_path} (0600, {cred_mode})")

    # 4) group ownership + setgid (best-effort, like aba-preflight)
    try:
        gid = grp.getgrnam(a.group).gr_gid
        try:
            os.chown(group_root, -1, gid)
        except PermissionError:
            print(f"note: not permitted to chgrp {group_root} → {a.group} (not a member?)")
        os.chmod(group_root, 0o2775)
    except KeyError:
        print(f"note: unix group {a.group!r} not found — left ownership as-is")

    print(f"✓ enrolled '{a.group}' — it now appears on the launch form and passes preflight.")


if __name__ == "__main__":
    main()
