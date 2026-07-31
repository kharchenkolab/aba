"""The Policy interface — the load-bearing abstraction.

The engine folds events; at each editorial moment it calls
`policy.decide(moment)` and applies the returned ops through THE GATE.
Baselines now and the real LLM agent later implement the same interface.

Moment kinds (RUNNER_HANDOFF minimum plus `session_start`, added so a policy
can brief on re-entry and know the anchor at open):

  session_start   a sitting opened (the previous one was parked/filed already)
  finding_landed  a finding landed (foreground in the live sitting, or
                  background straight into sediment)
  gesture         a gesture was made; the engine has already applied its
                  mechanical semantics (salience marks; investigation verbs
                  compiled into typed plan items with provisional marks) —
                  the policy adds the discretionary part (routing a pin,
                  drafting on draft_claim, birthing intent stubs on expand)
  instruction     free-text instruction (the `expect` field on the event is
                  authoring guidance for graders — honest policies must not
                  read it; only the scripted-good policy is scenario-keyed)
  ratified        a ratify event was matched (moment.matched holds accepted
                  proposal ids; empty if the free text matched nothing —
                  recorded as a trace error already)
  dismissed       ditto for dismiss
  distill         "distill so far" — the policy may finalize routing rows /
                  raise proposals; AFTER this the engine settles all pending
                  routine rows (v1.1 consent semantics: distill accepts the
                  routine, veto-tier rows presented; decisions wait for ratify)
  clock           time advanced (sittings filed, fast clock ran)

`moment.expired` lists Class-2 proposals that lapsed at the most recent
timer-settlement boundary; timers settle when scientist attention returns
(see the absence rule in engine.py), so expiries ride the first
scientist-driven moment after the gap that consumed the timer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .events import Event, Finding, Pool, Scenario
from .ops import Op
from .state import RecordView


@dataclass
class Moment:
    kind: str
    event: Event
    state: RecordView
    pool: Pool
    finding: Finding | None = None      # pool metadata for finding/gesture targets
    matched: tuple[str, ...] = ()       # ratified/dismissed: matched proposal ids
    expired: tuple[str, ...] = ()       # clock: proposals that lapsed


class Policy(ABC):
    """An editorial policy.  One instance per run (may keep per-run state)."""

    name = "abstract"

    def reset(self, pool: Pool, scenario: Scenario) -> None:
        """Called once before replay.  Default: nothing."""

    @abstractmethod
    def decide(self, moment: Moment) -> list[Op]:
        """Return the ops to apply at this editorial moment."""
        raise NotImplementedError
