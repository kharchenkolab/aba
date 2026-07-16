"""Lazy-env-init: environment-boot.yml must be a faithful subset of environment.yml.

Under ABA_ENV_PREWARM=staged the installer creates the base from environment-boot.yml
(fast, minimal), starts the server, then completes it with
`micromamba env update -f environment.yml`. For the completed base to equal the eager
build, boot must be a strict SUBSET with identical pins + an identical pip: section.
(Post-W3.4 the science stack is weft-owned, in neither env — so completion now only
adds build helpers + typst; the subset invariant still holds.)
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


def test_science_stack_is_weft_owned_not_in_base():
    """W3.4 slim controller: the single-cell science + viewer stack is realized by
    weft in SESSION kernels/packs (install/core/envs/python_bio.yaml), NOT installed
    into the controller base — so it is absent from BOTH the boot and the full
    controller env. scipy joined the science stack (its only controller consumer was
    lstar-sc, which now subprocesses the SESSION python); lstar-sc/zarr (the viewer
    data layer) moved to the pack too. (Before W3.4 the full env carried it and
    staging deferred it; now neither micromamba env pulls it.)"""
    _, full_conda, full_pip = _split(FULL)
    _, boot_conda, boot_pip = _split(BOOT)

    def names(specs):
        import re
        return {re.split(r"[=<>! ]", s)[0] for s in specs}

    boot_names, full_names = names(boot_conda), names(full_conda)
    # science stack (conda) is weft-owned → in NEITHER controller env
    for weft_owned in ("scanpy", "anndata", "scvi-tools", "leidenalg", "scipy"):
        assert weft_owned not in boot_names, f"{weft_owned} is weft-owned (not in boot)"
        assert weft_owned not in full_names, f"{weft_owned} is weft-owned (not in the controller env)"
    # viewer data layer (pip) is weft-owned too — the backend subprocesses the
    # session python for .lstar.zarr work and serves stores as raw bytes
    for weft_owned in ("lstar-sc", "zarr"):
        assert weft_owned not in names(full_pip), f"{weft_owned} is weft-owned (python pack), not in controller pip:"
    # but the numeric core + kernel MUST be in boot (server can run python from boot)
    for need in ("numpy", "pandas", "ipykernel", "python", "nodejs"):
        assert need in boot_names, f"{need} must be in the boot env"
