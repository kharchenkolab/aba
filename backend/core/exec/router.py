"""ExecutionRouter — decides *where* a step runs (capabilities.md §10).

The placement decision is ABA's, never the individual tool/MCP server's:
declare → **decide** → place → run. P0 ships `LocalRouter`, which has one
backend ("local") but performs the threshold-check + override structure, so
adding HPC/cloud later (agent_advise §5.6) is a new branch, not a new concept.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class ExecutorChoice:
    location: str = "local"                 # "local" | future: "hpc:<part>", "cloud:<queue>"
    rationale: str = ""
    requires_approval: bool = False


class ExecutionRouter(Protocol):
    def route(
        self,
        *,
        estimate: Optional[dict] = None,    # {ram_gb, runtime_min, ...} from ResourceEstimator
        policy: Optional[dict] = None,      # project/institution caps + thresholds
        data_locality: str = "local",       # where the inputs physically are (data gravity)
        override: Optional[str] = None,      # user-forced location
    ) -> ExecutorChoice:
        ...


class LocalRouter:
    """One-VM router: everything runs locally. The structure (override honored,
    a place to compare an estimate against a threshold) is real; the outcome is
    singular until a second backend exists."""

    def route(
        self,
        *,
        estimate: Optional[dict] = None,
        policy: Optional[dict] = None,
        data_locality: str = "local",
        override: Optional[str] = None,
    ) -> ExecutorChoice:
        # Explicit background request (agent flag) — honored directly.
        if override == "background":
            return ExecutorChoice(location="background",
                                  rationale="explicit background request")
        # A forced target other than local/background has no backend yet.
        if override and override not in ("local", "background"):
            return ExecutorChoice(location=override,
                                  rationale=f"override → {override} (no backend yet; future seam)",
                                  requires_approval=True)
        # Threshold: a long estimated runtime auto-routes to the background
        # job queue (the single-VM analog of HPC). Short runs stay synchronous.
        runtime_min = float((estimate or {}).get("runtime_min") or 0)
        threshold_min = float((policy or {}).get("background_threshold_min", 4))
        if runtime_min and runtime_min >= threshold_min:
            return ExecutorChoice(
                location="background",
                rationale=f"est runtime {runtime_min:.0f}m ≥ {threshold_min:.0f}m → background")
        return ExecutorChoice(location="local", rationale="short/synchronous")
