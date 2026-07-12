"""Module registry — the declarative catalog of capability packs (misc/modules.md).

Pure data: no imports of runtime state, so it's cheap and safe to import anywhere.
Readiness probes + install execution live in manager.py / reconciler.py, keyed by
module id, so this stays a plain manifest.

Each module has one of three STATES (misc/modules.md):
  • on         → installed at boot (reconciler); proactive.
  • first_use  → not at boot; auto-installs the first time the capability is used.
  • off        → never auto-installs; a request is refused with a nudge to enable it.

Defaults (decisions 2026-07-11): python-bio ON, r-bio + viewer-pagoda3 FIRST_USE on
personal installs. Per-target overrides (OOD/cluster eager → all ON) are applied by
the installer writing the initial modules.json — not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STATES = ("on", "first_use", "off")


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    description: str
    size: str                      # human estimate, e.g. "~2 GB"
    est_time: str                  # human estimate, e.g. "3–5 min"
    default_state: str             # on | first_use | off (the state when unset in modules.json)
    env_target: str                # base-update | conda-tools | assets
    install_script: str            # repo-relative script the reconciler/installer runs
    removable: bool                # can be uninstalled to reclaim disk (base-update is not)
    first_use: tuple[str, ...] = field(default_factory=tuple)  # trigger hints (import names / viewer types / file exts)


# Order = display order in Settings → Modules.
MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec(
        id="python-bio",
        title="Python analysis stack",
        description="scanpy, anndata, scvi-tools (PyTorch), leidenalg, UMAP — single-cell / "
                    "genomics analysis in Python.",
        size="~3 GB",
        est_time="4–8 min",
        default_state="on",
        env_target="base-update",
        install_script="install/core/modules/install-python-bio.sh",
        removable=False,           # lives in the read-only base; removal = rebuild boot
        first_use=("scanpy", "anndata", "scvi", "scvi_tools", "leidenalg", "umap"),
    ),
    ModuleSpec(
        id="r-bio",
        title="R toolchain",
        description="R 4.4 + Seurat + Bioconductor (DESeq2/edgeR/limma) + the lstar R viewer "
                    "bridge — R-based analysis and .rds viewing.",
        size="~4 GB",
        est_time="~2–5 min",         # conda binaries (Seurat/Bioc) — measured ~90s + lstar-r compile
        default_state="first_use",
        env_target="conda-tools",
        install_script="install/core/modules/install-r-bio.sh",
        removable=True,
        first_use=("seurat", "bioconductor", ".rds"),
    ),
    ModuleSpec(
        id="viewer-pagoda3",
        title="pagoda3 viewer",
        description="Interactive browser viewer for large single-cell embeddings "
                    "(.lstar.zarr). The reader/converter ships in core; this is the viewer bundle.",
        size="~40 MB",
        est_time="under 1 min",
        default_state="first_use",
        env_target="assets",
        install_script="install/core/modules/install-viewer-pagoda3.sh",
        removable=True,
        first_use=("pagoda3", ".lstar.zarr"),
    ),
)

_BY_ID = {m.id: m for m in MODULES}


def all_modules() -> tuple[ModuleSpec, ...]:
    return MODULES


def get(module_id: str) -> ModuleSpec | None:
    return _BY_ID.get(module_id)


def ids() -> tuple[str, ...]:
    return tuple(_BY_ID)
