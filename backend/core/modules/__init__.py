"""Modules — capability packs beyond the platform core (misc/modules.md).

The installer builds only platform CORE; domain capability (the scientific Python
stack, the R toolchain, viewers) ships as MODULES the running backend reconciles in
the background. This package holds the declarative registry, the per-deployment state
file ($ABA_HOME/modules.json), the read-only manager (list + resolve state), and the
reconciler that installs enabled-but-missing modules post-start.
"""
