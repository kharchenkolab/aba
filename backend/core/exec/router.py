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
        if override and override != "local":
            # A forced non-local target is recorded but flagged — there is no
            # backend to honor it yet, so approval/visibility is required.
            return ExecutorChoice(
                location=override,
                rationale=f"user override → {override} (no backend yet; P5+)",
                requires_approval=True,
            )
        return ExecutorChoice(
            location="local",
            rationale="single-VM: all work runs locally",
        )
