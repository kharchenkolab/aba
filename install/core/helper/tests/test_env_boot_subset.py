"""Lazy-env-init: environment-boot.yml must be a faithful subset of environment.yml.

Under ABA_ENV_PREWARM=staged the installer creates the base from environment-boot.yml
(fast, minimal), starts the server, then completes it with
`micromamba env update -f environment.yml`. For the completed base to equal the eager
build, boot must be a strict SUBSET with identical pins + an identical pip: section —
and it must actually EXCLUDE the heavy stack (else staging saves nothing).
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
FULL = REPO_ROOT / "install/core/environment.yml"
BOOT = REPO_ROOT / "install/core/environment-boot.yml"


def _split(path: Path):
    doc = yaml.safe_load(path.read_text())
    conda, pip = [], []
    for dep in doc["dependencies"]:
        if isinstance(dep, dict) and "pip" in dep:
            pip = list(dep["pip"])
        else:
            conda.append(str(dep))
    return doc, conda, pip


def test_boot_is_strict_subset_with_matching_pins():
    full_doc, full_conda, full_pip = _split(FULL)
    boot_doc, boot_conda, boot_pip = _split(BOOT)

    # same env identity + channels (so the completion `env update` resolves consistently)
    assert boot_doc["name"] == full_doc["name"]
    assert boot_doc["channels"] == full_doc["channels"]

    # every boot conda dep appears VERBATIM in full (same pin string) — a subset
    missing = [d for d in boot_conda if d not in full_conda]
    assert not missing, f"boot conda deps not in full (pin drift?): {missing}"

    # the pip: section is boot-tier in its entirety → must be identical
    assert boot_pip == full_pip, "boot pip: section must match environment.yml exactly"


def test_boot_excludes_the_heavy_stack():
    _, full_conda, _ = _split(FULL)
    _, boot_conda, _ = _split(BOOT)

    def names(specs):
        import re
        return {re.split(r"[=<>! ]", s)[0] for s in specs}

    boot_names, full_names = names(boot_conda), names(full_conda)
    # the deferred tiers must NOT be in boot, but MUST be in full
    for heavy in ("scanpy", "anndata", "scvi-tools", "leidenalg"):
        assert heavy not in boot_names, f"{heavy} should be deferred (not in boot)"
        assert heavy in full_names, f"{heavy} missing from environment.yml"
    # but the ABI core + kernel MUST be in boot (server can run python from boot)
    for need in ("numpy", "pandas", "scipy", "ipykernel", "python", "nodejs"):
        assert need in boot_names, f"{need} must be in the boot env"
