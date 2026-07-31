"""Scripted-good policy — the sanity ceiling.

Hand-written for exactly ONE run: pool `checkout-p99-lb-idle-timeout`,
scenario `contradiction`.  It is keyed to that event stream (dispatch by the
authored `t` values) and does what the scenario's assertions expect:

* the onset claim draft (draft_claim on F03 at t=7) is proposed as a decision
  and ratified into Q1 prose at t=8;
* the alternatives gesture on F05 (t=13) is compiled by the engine; F05 wears
  its provisional mark until the item lands;
* F20's overturn of the RATIFIED onset (t=22) is the Class-X interrupt: the
  ratified prose is never rewritten — a dated, provenance-linked addendum
  proposal is raised the same cycle (decisive evidence bypasses the wait, not
  the ceremony) and stays pending to the end (no second ratify exists in the
  stream); the unratified onset note is revised with provenance; F03 is
  marked superseded-but-findable; and a cascade re-examination item is raised
  against the still-committed pricing attribution;
* F15-over-F10 and F16-over-F05 (t=26/27) are Class-1/2 absorptions of
  never-ratified prose: revise with provenance, mark superseded, never
  delete;
* F16's landing discharges BOTH open scrutiny items on F05 (the alternatives
  gesture item and the cascade re-exam item) through a routing row that
  names them — subsumption is proposed, not silent, and the provisional
  marks clear when the row is accepted at the t=30 distill.

Running it against any other (pool, scenario) raises ValueError.
"""

from __future__ import annotations

from . import ops as O
from .policy import Moment, Policy

POOL_ID = "checkout-p99-lb-idle-timeout"
SCENARIO_ID = "contradiction"


class ScriptedGood(Policy):
    name = "scripted_good"

    def reset(self, pool, scenario) -> None:
        if pool.id != POOL_ID or scenario.id != SCENARIO_ID:
            raise ValueError(
                f"scripted_good is keyed to {POOL_ID}/{SCENARIO_ID}, "
                f"not {pool.id}/{scenario.id}")

    def decide(self, moment: Moment) -> list[O.Op]:
        if moment.kind == "ratified":
            return [O.ApplyConsented(proposal_id=pid) for pid in moment.matched]
        handler = getattr(self, f"_t{moment.event.t}", None)
        return handler(moment) if handler else []

    # -- sitting 1 (Q1): impact --------------------------------------------

    def _t2(self, m):   # F01
        return [
            O.CreateSection(question_id="Q1", title="Impact",
                            section_kind="stub", cls="1"),
            O.RouteFinding(finding_id="F01", stratum="story", questions=("Q1",)),
            O.WriteProse(section="Q1/Impact",
                         text="Checkout p99 regressed 5.2x at weekday peak "
                              "(310 to 1,620 ms) while p50 is flat.",
                         maturity="cross-checked", provenance=("F01",)),
        ]

    def _t3(self, m):   # F02
        return [
            O.RouteFinding(finding_id="F02", stratum="story", questions=("Q1",)),
            O.WriteProse(section="Q1/Impact",
                         text="Edge 504s run 18x elevated (0.9% of peak "
                              "attempts), still below the 5% alert threshold.",
                         maturity="supported", provenance=("F02",)),
        ]

    def _t4(self, m):   # F03 — the (doomed) onset reading
        return [
            O.RouteFinding(finding_id="F03", stratum="story", questions=("Q1",)),
            O.WriteProse(section="Q1/Impact",
                         text="Apparent onset Jun 16 ~15:00 UTC from the "
                              "daily rollup (24h grain).",
                         maturity="conjecture", provenance=("F03",)),
        ]

    def _t5(self, m):   # F04
        return [O.RouteFinding(finding_id="F04", stratum="story",
                               questions=("Q1", "Q2"))]

    def _t6(self, m):   # F06
        return [
            O.RouteFinding(finding_id="F06", stratum="story", questions=("Q1",)),
            O.WriteProse(section="Q1/Impact",
                         text="Peak histogram is bimodal and quantized: 4.3% "
                              "slow-or-error, slow cluster 1.52-1.75 s.",
                         maturity="supported", provenance=("F06",)),
        ]

    def _t7(self, m):   # draft_claim on F03
        return [O.Propose(
            proposal_kind="claim_draft",
            proposal_cls="3",
            description="claim draft: the Jun 16 onset claim — daily rollup "
                        "places the regression onset Jun 16 ~15:00 UTC — "
                        "into the Q1 impact section",
            payload=(O.WriteProse(
                section="Q1/Impact",
                text="Onset: the regression begins Jun 16 ~15:00 UTC "
                     "(daily-rollup step Jun 15 to 16).",
                maturity="conjecture", provenance=("F03",), ratified=True),),
        )]

    # -- sitting 2 (Q2): attribution -----------------------------------------

    def _t12(self, m):  # F05 — the pricing suspect
        return [
            O.CreateSection(question_id="Q2", title="Attribution",
                            section_kind="stub", cls="1"),
            O.RouteFinding(finding_id="F05", stratum="story", questions=("Q2",)),
            O.WriteProse(section="Q2/Attribution",
                         text="Leading suspect: pricing-svc v341 (deployed "
                              "Jun 16 14:20 UTC), the only order-path change "
                              "near the apparent onset.",
                         maturity="supported", provenance=("F05",)),
        ]

    def _t14(self, m):  # F07 — the tension
        return [
            O.RouteFinding(finding_id="F07", stratum="story",
                           questions=("Q1", "Q2")),
            O.WriteProse(section="Q2/Attribution",
                         text="Tension: span deltas put the whole excess in "
                              "the ledger phase; pricing +40 ms cannot "
                              "account for it.",
                         maturity="supported", provenance=("F07",)),
        ]

    def _t15(self, m):  # F08
        return [O.RouteFinding(finding_id="F08", stratum="story",
                               questions=("Q2",))]

    def _t16(self, m):  # F14 — mechanism signal
        return [
            O.CreateSection(question_id="Q2", title="Mechanism",
                            section_kind="stub", cls="1"),
            O.RouteFinding(finding_id="F14", stratum="story", questions=("Q2",)),
            O.WriteProse(section="Q2/Mechanism",
                         text="The excess is quantized (~1,521 ms) in the "
                              "connection-acquire phase.",
                         maturity="cross-checked", provenance=("F14",)),
        ]

    def _t17(self, m):  # F10 — the false fix
        return [
            O.RouteFinding(finding_id="F10", stratum="story", questions=("Q2",)),
            O.WriteProse(section="Q2/Attribution",
                         text="Rolling back v341 on canary appears to clear "
                              "the tail — read as supporting the pricing "
                              "attribution.",
                         maturity="supported", provenance=("F10",)),
        ]

    # -- sitting 3 (Q1): the interrupt ---------------------------------------

    def _t21(self, m):  # F11
        return [
            O.RouteFinding(finding_id="F11", stratum="story",
                           questions=("Q1", "Q2")),
            O.WriteProse(section="Q2/Attribution",
                         text="Relapse: the tail returns at next peak even "
                              "though the rollback held.",
                         maturity="supported", provenance=("F11",)),
        ]

    def _t22(self, m):  # F20 overturns F03 — ratified prose: Class X
        ops: list[O.Op] = [
            O.RouteFinding(finding_id="F20", stratum="story",
                           questions=("Q1", "Q2")),
            O.MarkSuperseded(finding_id="F03", by="F20",
                             note="per-AZ reconstruction supersedes the "
                                  "rollup onset reading"),
        ]
        # revise the unratified onset note with provenance
        for block in m.state.prose_blocks(citing="F03", ratified=False):
            if block["superseded_by"] is None and not block["authored"]:
                ops.append(O.ReviseProse(
                    prose_id=block["id"],
                    text="Onset revised: per-AZ reconstruction places the "
                         "true onset Jun 12 (F20); the Jun-16 step was a "
                         "24h-rollup artifact.",
                    provenance=("F20",)))
        # the ratified onset prose: dated addendum PROPOSAL, never a rewrite
        for block in m.state.prose_blocks(citing="F03", ratified=True):
            if block["superseded_by"] is None:
                ops.append(O.Propose(
                    proposal_kind="addendum",
                    proposal_cls="X",
                    description="dated addendum to the ratified Jun-16 onset "
                                "prose: F20's per-AZ reconstruction "
                                "supersedes the Jun-16 face-value onset; "
                                "true onset Jun 12",
                    payload=(O.AddAddendum(
                        prose_id=block["id"],
                        text="Addendum: F20 supersedes the Jun-16 onset "
                             "reading; the true onset is Jun 12 (per-AZ "
                             "p99 reconstruction).",
                        provenance=("F20", "F03")),),
                ))
        # cascade: the pricing attribution was anchored on that onset and is
        # still committed — visible re-examination, raised the same cycle
        ops.append(O.CreatePlanItem(
            owner="Q2", item_kind="check",
            text="re-examine the pricing attribution (F05): it was anchored "
                 "on the Jun-16 onset, which F20 just superseded",
            target="F05", state="proposed"))
        return ops

    # -- sitting 4 (Q2): the exonerations ------------------------------------

    def _t26(self, m):  # F15 overturns F10 — unratified: Class-1/2 absorption
        ops: list[O.Op] = [
            O.RouteFinding(finding_id="F15", stratum="story", questions=("Q2",)),
            O.MarkSuperseded(finding_id="F10", by="F15",
                             note="restart-signature analysis supersedes the "
                                  "rollback-fix reading"),
        ]
        for block in m.state.prose_blocks(citing="F10", ratified=False):
            if block["superseded_by"] is None and not block["authored"]:
                ops.append(O.ReviseProse(
                    prose_id=block["id"],
                    text="Revised: the rollback 'fix' was the bundled pod "
                         "restart; the canary relapse matches idle-flow "
                         "re-arming (F15 supersedes the F10 reading).",
                    provenance=("F15",)))
        return ops

    def _t27(self, m):  # F16 overturns F05 — and discharges both scrutiny items
        discharges = tuple(
            item["id"]
            for item in m.state.plan_items(target="F05")
            if item["state"] not in ("produced", "absorbed"))
        ops: list[O.Op] = [
            O.RouteFinding(
                finding_id="F16", stratum="story", questions=("Q2",),
                note="re-canary isolates v341 at +40 ms benign; closes the "
                     "rival-explanations item and the onset-cascade re-exam",
                discharges=discharges),
            O.MarkSuperseded(finding_id="F05", by="F16",
                             note="re-canary exonerates pricing v341"),
        ]
        for block in m.state.prose_blocks(citing="F05", ratified=False):
            if block["superseded_by"] is None and not block["authored"]:
                ops.append(O.ReviseProse(
                    prose_id=block["id"],
                    text="Revised: the re-canary isolates v341 at +40 ms "
                         "benign — the pricing attribution is exonerated "
                         "(F16 supersedes the F05 reading).",
                    provenance=("F16",)))
        return ops

    def _t28(self, m):  # F21
        return [
            O.RouteFinding(finding_id="F21", stratum="story",
                           questions=("Q1", "Q2")),
            O.WriteProse(section="Q2/Mechanism",
                         text="Counter-scale confirmation: 4.2% compound "
                              "exposure matches the observed slow-or-error "
                              "share.",
                         maturity="supported", provenance=("F21",)),
        ]

    def _t29(self, m):  # F27
        return [O.RouteFinding(finding_id="F27", stratum="story",
                               questions=("Q1", "Q2"))]
