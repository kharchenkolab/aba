"""Module registry — the declarative catalog of capability packs (misc/modules.md).

Pure data: no imports of runtime state, so it's cheap and safe to import anywhere.
Readiness probes + install execution live in manager.py / reconciler.py, keyed by
module id, so this stays a plain manifest.

Defaults (decisions 2026-07-11): python-bio ON (secondary wave right after boot),
r-bio and viewer-pagoda3 OFF (first-use / manual toggle) on personal installs.
Per-target overrides (OOD/cluster eager) are applied by the installer writing the
initial modules.json — not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    title: str
    description: str
    size: str                      # human estimate, e.g. "~2 GB"
    est_time: str                  # human estimate, e.g. "3–5 min"
    default_enabled: bool          # on → reconciled right after boot; off → manual/first-use
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
        default_enabled=True,
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
        size="~5 GB",
        est_time="15–30 min",
        default_enabled=False,
        env_target="conda-tools",
        install_script="install/core/modules/install-r-bio.sh",
        removable=True,
        first_use=("seurat", "bioconductor", ".rds"),
    ),
    ModuleSpec(
        id="viewer-pagoda3",
        title="pagoda3 viewer",
        description="Interactive browser viewer for large single-cell embeddings "
                    "(.lstar.zarr) — the pagoda3 dist + its reader.",
        size="~150 MB",
        est_time="under 1 min",
        default_enabled=False,
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
