"""Deploy forward-loop drift guard (env_reorg Phase 5).

The OOD launcher (install/ood/aba/template/script.sh.erb) forwards a set of env
vars into the backend process via `apptainer ... --env`. That set MUST equal the
registry's `deploy_injected` surface — otherwise "add a deploy var, forget to
forward it" silently breaks a containerized deploy (the desync the next.fatagain
fold kept hitting across several shell templates).

ERB is rendered by OOD (Ruby) at launch, so it can't call the Python registry
directly; instead the registry is the single source of truth and THIS test asserts
the .erb mirrors `config.deploy_injected_keys()`. Regenerate the canonical list any
time with `python -m aba_installer.cli deploy-env` (or read deploy_injected_keys()).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

import core.config as config  # noqa: E402

ERB = (Path(__file__).resolve().parent.parent
       / "install" / "ood" / "aba" / "template" / "script.sh.erb")

# Vars the launcher forwards that are NOT backend settings (launcher runtime /
# third-party scheduler contract). Not part of the registry surface.
LAUNCHER_ONLY = {"ABA_PORT", "SLURM_CONF", "LD_LIBRARY_PATH", "TMPDIR"}


def _forwarded_env_vars(text: str) -> set[str]:
    """Every env var the template forwards via --env, from both the explicit
    `--env "NAME=..."` forms and the `for v in <list>; do ... --env "$v=..."` loop."""
    found = set(re.findall(r'--env\s+"([A-Z][A-Z0-9_]+)=', text))
    for m in re.finditer(r'for v in (.+?);\s*do', text, re.S):
        block = m.group(1)
        # only treat it as a forward loop if its body actually forwards $v
        found.update(re.findall(r'\bABA_[A-Z0-9_]+\b', block))
        found.update(re.findall(r'\b(?:ANTHROPIC_API_KEY|CLAUDE_CODE_OAUTH_TOKEN)\b', block))
    return found


def test_forward_loop_mirrors_registry():
    assert ERB.exists(), f"missing launcher template: {ERB}"
    text = ERB.read_text()
    forwarded = _forwarded_env_vars(text) - LAUNCHER_ONLY
    declared = set(config.deploy_injected_keys())

    missing = declared - forwarded   # deploy_injected but launcher doesn't forward
    extra = forwarded - declared     # launcher forwards but not a deploy_injected setting

    assert not missing, (
        "These deploy_injected settings are NOT forwarded by script.sh.erb "
        f"(add them to the forward-loop): {sorted(missing)}")
    assert not extra, (
        "script.sh.erb forwards these env vars that are neither a deploy_injected "
        "setting nor a known launcher-only var (declare them deploy_injected in "
        f"config.py or add to LAUNCHER_ONLY): {sorted(extra)}")
