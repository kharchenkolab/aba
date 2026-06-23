"""Bundle scope resolution.

Figures out, at startup, which bundle scopes exist in this deployment +
where they live. Also resolves user identity, group, and state/scratch
paths since those need to happen anyway (run-folder placement,
future credential resolution).

This module is intentionally scope-count-agnostic — it produces an
ORDERED LIST of `ScopeBundle` entries that downstream code (the
bundle loader in core/bundle/loader.py) walks generically. Adding
future scopes (consortium, project-group, etc.) is a matter of
appending entries to the chain here; no other module changes shape.

See:
  - misc/bundle.md         — architecture overview
  - misc/bundle_layering.md — composition rules (the loader's job)
  - misc/bundle_plan.md    — implementation phase plan
  - misc/site-config.md    — per-install site.yaml schema

Defaults: on a fresh Mac with no env vars + no site.yaml, this returns
a scope chain of [system, user] (user is optional but resolution
always names a candidate path).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# -----------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------

@dataclass
class ScopeBundle:
    """One entry in the scope chain.

    `path` may or may not exist on disk — `present` records the runtime
    check at resolution time. The loader walks the chain and skips
    entries with `present=False` (unless they auto-created during
    resolution, in which case the path now exists).

    `optional=False` means it's a hard error if the scope's path is
    missing. Only `system` is currently non-optional.
    """
    name: str
    label: str
    path: Path
    present: bool
    optional: bool = True


@dataclass
class ScopeResolution:
    """The full result of resolve_scopes(). Consumed by the bundle loader,
    and also handy for non-bundle concerns (project state dir,
    credential probe paths, etc.)."""
    user: str
    group: str | None
    scope_chain: list[ScopeBundle]          # broadest-first
    state_dir: Path                          # per-user state (projects, runs)
    scratch_dir: Path | None
    site_config: dict[str, Any] | None      # parsed site.yaml or None
    composed_bundle: Path | None             # set when a pre-composed bundle is configured (future)
    auto_created: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _read_site_yaml(path: Path | None) -> dict | None:
    """Read site.yaml if path is provided + readable; else None."""
    if not path or not path.is_file():
        return None
    try:
        import yaml
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return None


def _expand_placeholders(template: str | None, *,
                          user: str, group: str | None,
                          home: Path) -> str | None:
    """Expand {user}, {group}, {home} in a path template.
    Returns None if the template references {group} but group is None.
    """
    if template is None:
        return None
    if "{group}" in template and group is None:
        return None
    return (template
            .replace("{user}", user)
            .replace("{group}", group or "")
            .replace("{home}", str(home)))


def _resolve_group(env: dict[str, str]) -> str | None:
    """Determine the user's effective group.

    Resolution order (first non-empty wins):
      1. $ABA_GROUP env var (explicit override)
      2. $OOD_FORM_aba_lab env var (set by an Open OnDemand form)
      3. unix primary group (via getent / os.getgroups via grp module)
      4. None
    """
    for key in ("ABA_GROUP", "OOD_FORM_aba_lab"):
        v = env.get(key)
        if v and v.strip():
            return v.strip()
    try:
        import grp
        import pwd
        user = env.get("USER") or pwd.getpwuid(os.getuid()).pw_name
        gid = pwd.getpwnam(user).pw_gid
        primary = grp.getgrgid(gid).gr_name
        # 'users' / numeric / system-defaults aren't useful as a lab id.
        if primary and primary not in ("users", "nogroup", "staff"):
            return primary
    except Exception:
        pass
    return None


def _user_id(env: dict[str, str]) -> str:
    for key in ("USER", "USERNAME", "LOGNAME"):
        v = env.get(key)
        if v:
            return v
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return "unknown"


def _mkdir_if_allowed(path: Path, *, allowed: bool, warnings: list[str],
                      auto_created: list[Path], label: str) -> bool:
    """Create `path` if it doesn't exist, but only when allowed.
    Returns True iff the path exists after the call (either it already
    did or we created it). Records `auto_created`."""
    if path.exists():
        return True
    if not allowed:
        warnings.append(f"{label} not present at {path}; auto-create disabled")
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        auto_created.append(path)
        return True
    except Exception as e:
        warnings.append(f"failed to create {label} at {path}: {e}")
        return False


def _candidate_path(env_var: str, env: dict[str, str], default: Path | None,
                     *, user: str, group: str | None,
                     home: Path) -> Path | None:
    """Resolve a scope path: env var wins, then default (with placeholder
    expansion). Returns None if neither resolves (e.g. default uses
    {group} but group is unknown)."""
    v = env.get(env_var)
    if v and v.strip():
        return Path(_expand_placeholders(v.strip(), user=user, group=group, home=home)
                    or v.strip()).expanduser().resolve()
    if default is None:
        return None
    expanded = _expand_placeholders(str(default), user=user, group=group, home=home)
    return Path(expanded).expanduser().resolve() if expanded else None


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

# Default labels for the v1 scope names. Site config can override.
_DEFAULT_LABELS = {
    "system":      "System",
    "institution": "Institution",
    "lab":         "Lab",
    "group":       "Group",   # alias for lab when site config names it that way
    "user":        "Your",
}


def resolve_scopes(env: dict[str, str] | None = None,
                    site_config_path: Path | str | None = None,
                    *,
                    system_bundle_default: Path | None = None,
                    auto_create: bool = True) -> ScopeResolution:
    """Resolve the scope chain + state paths for this deployment.

    Parameters
    ----------
    env
        Environment mapping (defaults to os.environ).
    site_config_path
        Optional path to a site.yaml file (defaults to $ABA_SITE_CONFIG
        or /cluster/aba/site.yaml). Missing/unreadable → use built-in
        defaults.
    system_bundle_default
        Where to look for the system bundle when no env var sets it.
        Defaults to <repo>/backend/system_bundle/ (P4 will populate it).
    auto_create
        Whether to mkdir state dirs + user bundle dir when missing.

    Returns
    -------
    ScopeResolution
        Ordered chain + state paths + provenance.
    """
    env = dict(env if env is not None else os.environ)
    home = Path(env.get("HOME") or os.path.expanduser("~")).expanduser().resolve()
    user = _user_id(env)
    group = _resolve_group(env)
    warnings: list[str] = []
    auto_created: list[Path] = []

    # Site config
    if site_config_path is None:
        site_config_path = env.get("ABA_SITE_CONFIG") or "/cluster/aba/site.yaml"
    site_config = _read_site_yaml(Path(site_config_path))

    # ---- System scope (always present, but may not yet be populated)
    if system_bundle_default is None:
        # Default to the repo's backend/system_bundle/ — P4 populates this.
        # backend/core/bundle/scope_resolver.py — parents: bundle, core, backend
        system_bundle_default = Path(__file__).resolve().parents[2] / "system_bundle"
    system_path = _candidate_path(
        "ABA_SYSTEM_BUNDLE", env, system_bundle_default,
        user=user, group=group, home=home,
    )
    # System is the only non-optional scope; if missing, warn but don't
    # block — pre-P4 deployments don't have backend/system_bundle/ yet.
    system_present = bool(system_path and system_path.is_dir())
    if not system_present and system_path is not None:
        warnings.append(f"system bundle not found at {system_path} "
                        f"(P4 system-bundle refactor not yet applied)")
    system_scope = ScopeBundle(
        name="system",
        label=_DEFAULT_LABELS["system"],
        path=system_path or Path("/dev/null"),
        present=system_present,
        optional=False,
    )

    # ---- Installation scope (the deployment's own bundle; named "institution"
    # in the chain). ALWAYS present in the chain (present iff the dir exists):
    # every install has an installation bundle — that's where the imported recipe
    # pack + site-wide policy live, so even a solo Mac gets the cookbook there.
    # Defaults to {home}/.aba/installation (the single user IS the installation);
    # a cluster points it at /cluster/aba/installation via site.yaml.
    inst_default = home / ".aba" / "installation"
    if site_config:
        inst_default_path = (site_config.get("scopes") or {}) \
            .get("institution", {}).get("bundle_path")
        if inst_default_path:
            inst_default = Path(_expand_placeholders(
                str(inst_default_path), user=user, group=group, home=home)
                or str(inst_default_path))
    institution_path = _candidate_path(
        "ABA_INSTITUTION_BUNDLE", env, inst_default,
        user=user, group=group, home=home,
    )
    institution_scope = None
    if institution_path is not None:
        institution_scope = ScopeBundle(
            name="institution",
            label=(site_config or {}).get("site", {}).get("name")
                  or _DEFAULT_LABELS["institution"],
            path=institution_path,
            present=institution_path.is_dir(),
            optional=True,
        )

    # ---- Lab / Group scope (optional, depends on group resolution)
    lab_default = None
    if site_config and (site_config.get("scopes") or {}).get("group", {}).get("enabled"):
        group_cfg = site_config["scopes"]["group"]
        root_template = group_cfg.get("root_path")
        bundle_subdir = group_cfg.get("bundle_subdir", "bundle")
        if root_template and "{group}" in root_template and group is None:
            warnings.append("lab scope configured but no group resolved")
        elif root_template:
            expanded = _expand_placeholders(
                root_template, user=user, group=group, home=home)
            if expanded:
                lab_default = Path(expanded) / bundle_subdir
    lab_path = _candidate_path(
        "ABA_LAB_BUNDLE", env, lab_default,
        user=user, group=group, home=home,
    )
    lab_scope = None
    if lab_path is not None:
        # Auto-create the group root (the dir that contains the bundle)
        # if site config asks for it.
        if site_config and (site_config.get("scopes") or {}).get("group", {}).get("auto_create_skeleton"):
            _mkdir_if_allowed(
                lab_path.parent, allowed=auto_create,
                warnings=warnings, auto_created=auto_created,
                label="group root",
            )
        lab_label = (site_config or {}).get("scopes", {}).get("group", {}).get("label") \
                    or (f"Lab ({group})" if group else _DEFAULT_LABELS["lab"])
        lab_scope = ScopeBundle(
            name="lab",
            label=lab_label,
            path=lab_path,
            present=lab_path.is_dir(),
            optional=True,
        )

    # ---- User scope (optional but resolved to a candidate path)
    user_default = home / ".aba" / "bundle"
    if site_config:
        ud = (site_config.get("scopes") or {}).get("user", {}).get("home_dir")
        if ud:
            expanded = _expand_placeholders(
                ud, user=user, group=group, home=home)
            if expanded:
                user_default = Path(expanded) / "bundle"
    user_path = _candidate_path(
        "ABA_USER_BUNDLE", env, user_default,
        user=user, group=group, home=home,
    )
    user_scope = None
    if user_path is not None:
        user_scope = ScopeBundle(
            name="user",
            label=_DEFAULT_LABELS["user"] + " preferences",
            path=user_path,
            present=user_path.is_dir(),
            optional=True,
        )

    # ---- State + scratch paths (NOT part of the bundle, but resolved here
    # since the same env/site_config drives them)
    state_default = home / ".aba" / "state"
    if site_config:
        sd = (site_config.get("scopes") or {}).get("user", {}).get("state_dir")
        if sd:
            expanded = _expand_placeholders(
                sd, user=user, group=group, home=home)
            if expanded:
                state_default = Path(expanded)
    state_dir = _candidate_path(
        "ABA_STATE_DIR", env, state_default,
        user=user, group=group, home=home,
    ) or state_default
    _mkdir_if_allowed(
        state_dir, allowed=auto_create,
        warnings=warnings, auto_created=auto_created, label="state_dir",
    )

    scratch_default = None
    if site_config:
        sd = (site_config.get("scopes") or {}).get("user", {}).get("scratch_dir")
        if sd:
            expanded = _expand_placeholders(
                sd, user=user, group=group, home=home)
            if expanded:
                scratch_default = Path(expanded)
    scratch_dir = _candidate_path(
        "ABA_SCRATCH", env, scratch_default,
        user=user, group=group, home=home,
    )
    if scratch_dir is not None:
        _mkdir_if_allowed(
            scratch_dir, allowed=auto_create,
            warnings=warnings, auto_created=auto_created, label="scratch_dir",
        )

    # ---- Composed bundle path (future-only marker)
    composed_str = env.get("ABA_COMPOSED_BUNDLE_PATH", "").strip()
    composed_bundle = Path(composed_str).expanduser().resolve() if composed_str else None

    # ---- Build scope chain — broadest-first.
    chain: list[ScopeBundle] = [system_scope]
    if institution_scope is not None:
        chain.append(institution_scope)
    if lab_scope is not None:
        chain.append(lab_scope)
    if user_scope is not None:
        chain.append(user_scope)

    return ScopeResolution(
        user=user,
        group=group,
        scope_chain=chain,
        state_dir=state_dir,
        scratch_dir=scratch_dir,
        site_config=site_config,
        composed_bundle=composed_bundle,
        auto_created=auto_created,
        warnings=warnings,
    )


# -----------------------------------------------------------------------
# Pretty-printer for startup log
# -----------------------------------------------------------------------

def format_resolution(r: ScopeResolution) -> str:
    """Single-block, human-readable summary of a resolution.
    Used by the startup-log emission + the `aba bundle inspect` CLI."""
    lines = []
    lines.append(f"[scope] user={r.user!r} group={r.group!r}")
    for s in r.scope_chain:
        flag = "OK" if s.present else "MISSING"
        lines.append(f"[scope] {s.name:<11} {flag:<7} {s.path}")
    lines.append(f"[scope] state_dir   {r.state_dir}")
    if r.scratch_dir:
        lines.append(f"[scope] scratch_dir {r.scratch_dir}")
    if r.composed_bundle:
        lines.append(f"[scope] composed    {r.composed_bundle}")
    for w in r.warnings:
        lines.append(f"[scope] WARN  {w}")
    for c in r.auto_created:
        lines.append(f"[scope] created   {c}")
    return "\n".join(lines)
