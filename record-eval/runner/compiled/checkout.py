"""Compiled assertions — pool checkout-p99-lb-idle-timeout (52)."""

from . import _mk

P = "checkout-p99-lb-idle-timeout"
COMPILED = []

COMPILED += _mk(P, "absence", [
    ("structure", "gap_record", (35, 35),
     dict(min_gap_days=21, briefing=True),
     "briefing presence + gap length checkable; 'ranked by consequence, "
     "past-tense, scaled to time away' is prose quality the trace cannot grade"),
    ("consent", "proposal_state", (35, 35),
     dict(desc_tokens=("compaction", "resolved", "summaries"), cls="2",
          status="pending", max_active_age=13),
     "timer-pause read as: still pending AND active_age under the 14-day "
     "expiry despite 21 calendar days elapsing"),
    ("structure", "ops_bounded", (34, 35),
     dict(structure_frozen=True),
     "'exactly as left' compiled as: no structural op applied across the gap "
     "window (the clock event and re-entry)"),
    ("plan", "plan_item_state", (36, 43),
     dict(kinds=("plan",), target="F35", min_state="produced"),
     "'resumes from the plan rather than re-derived context' is not "
     "observable; the lifecycle transition is the checkable part"),
    ("provenance", "gap_record", None,
     dict(min_gap_days=21, check_touched_last_sitting=True), ""),
    ("structure", "section_state", None,
     dict(question="Q3", min_cited=4,
          cites_any=("F29", "F30", "F32", "F37", "F31", "F40"),
          created_after_t=34),
     "'nothing pretends the section existed during the gap' compiled as "
     "created-after-gap for non-root Q3 sections"),
])

COMPILED += _mk(P, "busy-scientist", [
    ("plan", "plan_item_state", (16, 16),
     dict(kinds=("alternatives",), target="F05", min_state="planned",
          provisional_target=True), ""),
    ("structure", "sitting_index", (18, 24), dict(mode="coalesce"), ""),
    ("routing", "routing_destination", (24, 24),
     dict(spec={"F13": dict(allowed=("notes", "sediment"), pinned=True,
                            row_exists=True)}),
     "'agent proposes its destination visibly' compiled as a routing row "
     "existing for F13; 'never fades' holds by the pinned flag"),
    ("structure", "sitting_index", (30, 31), dict(mode="silent_filed"), ""),
    ("routing", "salience_state", (33, 35),
     dict(specs=[("F09", "faded_findable")]), ""),
    ("routing", "tray_state", None,
     dict(rows_for=("F13", "F17", "F09", "F01")),
     "'settled by place, not per episode' is organizational, not stateful; "
     "compiled as: a visible destination row exists for every gesture-named "
     "finding (kills drop-the-weak policies) with the typing partition "
     "holding throughout - per-sitting debt has no model structure to check"),
    ("consent", "consent_conservation", None,
     dict(no_auto_ratify=True,
          plan_still_open=(("check", "F17"), ("alternatives", "F05"))), ""),
    ("structure", "stub_is_plan", None,
     dict(question="Q3", intent=True, no_prose=True,
          require_hold_evaporated="F06"), ""),
])

COMPILED += _mk(P, "contradiction", [
    ("structure", "proposal_state", (7, 9),
     dict(kind="claim_draft", desc_tokens=("onset", "F03"), status="accepted",
          applied=True, require_ratified_prose_provenance=("F03",)),
     "'must go through the addendum grammar' is enforced by the gate; the "
     "checkable part is ratified prose backed by the accepted draft"),
    ("plan", "plan_item_state", (13, 13),
     dict(kinds=("alternatives",), target="F05", min_state="planned",
          provisional_target=True), ""),
    ("provenance", "overturn_handling", (22, 23),
     dict(pairs=[("F03", "F20")], mode="interrupt",
          cascade_reexam_target="F05"), ""),
    ("provenance", "overturn_handling", (26, 27),
     dict(pairs=[("F10", "F15"), ("F05", "F16")], mode="absorb"), ""),
    ("structure", "face_value_retired", (26, 30),
     dict(findings=("F03", "F05")), ""),
    ("consent", "consent_conservation", None,
     dict(no_auto_ratify=True, addenda_via_proposals=True), ""),
])

COMPILED += _mk(P, "flood", [
    ("structure", "tray_state", (24, 30),
     dict(non_empty=True, max_undifferentiated=9),
     "'batchable in one act' is a UI affordance; compiled as: the tray is "
     "non-empty at its in-window PEAK (rows settle at the t=30 distill, so "
     "the end snapshot is empty by construction) and stays bounded/typed"),
    ("structure", "sitting_index", (30, 30), dict(mode="mid_distill", at=30), ""),
    ("routing", "routing_destination", (29, 38),
     dict(spec={"F33": dict(forbid_questions=("Q2",)),
                "F34": dict(forbid_questions=("Q2",)),
                "F36": dict(allowed=("notes", "sediment"), faded=True)}),
     "'Q3/Q1 territory' for F33 compiled as its forbidden destination (Q2 "
     "story); positive destination is policy-dependent"),
    ("structure", "narrative_growth_bounded", (23, 39),
     dict(max_changes=3, min_changes=1),
     "the corpus parenthetical ('the mechanism section and the onset "
     "revision') implies at least one change - min_changes=1 kills the "
     "inert vacuous pass"),
    ("structure", "section_state", None,
     dict(question="Q2", cites_all=("F19", "F21", "F22", "F24", "F26"),
          require_draft_pending_target="F24"), ""),
    ("provenance", "sitting_index", None,
     dict(mode="leftovers", require_hold_evaporated="F21"),
     "'never pinned, noted, or discussed' is not fully derivable ('discussed' "
     "is undefined in the trace); compiled as leftovers-shelf non-loss plus "
     "hold evaporation"),
    ("consent", "consent_conservation", None, dict(no_auto_ratify=True), ""),
])

COMPILED += _mk(P, "interleaved", [
    ("routing", "section_state", (4, 7),
     dict(question="Q3", cites_any=("F29", "F34")), ""),
    ("routing", "cited_under_questions", (25, 25),
     dict(findings=("F20",), questions=("Q1", "Q2")),
     "'its overturn of F03 propagates regardless of the anchor' folds into "
     "the citation check; overturn handling is graded in contradiction"),
    ("routing", "cited_under_questions", (21, 28),
     dict(findings=("F21", "F27"), questions=("Q1", "Q2")),
     "impact-vs-mechanism framing of the two citations is prose content the "
     "placeholder-text model cannot grade"),
    ("structure", "section_state", None,
     dict(question="Q1", min_cited=2),
     "'developed in parallel, no duplicated prose' proxied by per-question "
     "citation mass (Q1 here; Q2/Q3 covered by assertions 1-2) plus the "
     "no-copies discipline of cited_under_questions"),
    ("consent", "routing_destination", None,
     dict(spec={"F20": dict(require_row_question="Q2")}),
     "'visible proposal or receipt' compiled as a routing row placing the "
     "Q1-sitting finding F20 under Q2 - the row is the receipt"),
    ("provenance", "sitting_index", None, dict(mode="touched"), ""),
])

COMPILED += _mk(P, "pivot", [
    ("structure", "section_state", (18, 25),
     dict(question="Q2", has_child_section=True,
          cites_any=("F05", "F10", "F12"),
          require_draft_pending_target="F10"),
     "'a real committed section' compiled as a full (non-stub) section under "
     "Q2 citing the pricing arc with the causal draft pending"),
    ("provenance", "overturn_handling", (30, 31),
     dict(pairs=[("F10", "F15"), ("F05", "F16")], mode="absorb",
          require_withdrawn_draft_target="F10"), ""),
    ("consent", "proposal_state", (30, 33),
     dict(desc_tokens=("pricing", "appendix", "demotion"), cls="3",
          status="accepted", accepted_at_t=32, applied=True), ""),
    ("structure", "section_state", None,
     dict(question="Q2", title_tokens=("pricing",), rank="appendix",
          cites_any=("F05", "F10"), nothing_deleted=True),
     "'+40 ms regression remains recorded as real-but-benign' is prose "
     "content; compiled as the appendixed section still citing its evidence"),
    ("provenance", "provenance_chain", None,
     dict(findings=("F15", "F16"), via_section=True, question="Q2"),
     "'one step from the appendixed section' compiled as section-chain "
     "reachability of the overturning findings"),
    ("salience", "face_value_retired", None, dict(findings=("F05", "F10")), ""),
])

COMPILED += _mk(P, "proactive-intent", [
    ("structure", "stub_is_plan", (22, 23),
     dict(question="Q4", intent=True, no_prose=True),
     "the failure-class direction is homed under Q4 (durability), the "
     "instruction's natural owner; 'evidence 0 of N' is the no-prose check"),
    ("plan", "plan_item_state", (22, 23),
     dict(owner="Q4", distinct_count=2, min_state="planned",
          text_tokens=()), ""),
    ("salience", "stub_is_plan", (28, 29),
     dict(question="Q4", intent=True, no_prose=True),
     "floor conversion: after the lead-with-it instruction the stub still "
     "carries no prose; 'refusal cited in evidence terms' is response prose "
     "the trace cannot grade"),
    ("plan", "plan_item_state", (32, 35),
     dict(owner="Q4", text_tokens=("conformance", "sweep"),
          min_state="absorbed"), ""),
    ("plan", "plan_item_state", (38, 40),
     dict(owner="Q4", text_tokens=("canary",), min_state="absorbed"), ""),
    ("structure", "section_state", None,
     dict(question="Q4", intent=True, min_cited=2),
     "'intent marker traceable declaration-to-chips' compiled as the intent "
     "flag surviving on the grown section with evidence cited"),
    ("consent", "ops_bounded", (23, 999),
     dict(max_cls="2"),
     "CORPUS/MODEL GAP: 'standing pre-consent' (§14.5) is not first-class in "
     "the engine; compiled as no op above Class 2 applied after the "
     "declaration - Class-3 growth would need explicit ratifies this stream "
     "does not contain"),
])

COMPILED += _mk(P, "slow-burn", [
    ("structure", "section_state", (13, 16),
     dict(question="Q2", no_child_section=True),
     "'no claim past conjecture while the check is open' folds into the "
     "maturity guard of assertion 1's provisional mark; primary check: no "
     "full section on the weak hint"),
    ("plan", "plan_item_state", (14, 15),
     dict(kinds=("check", "corroborate"), target="F17", distinct_count=2,
          provisional_target=True), ""),
    ("structure", "section_promotion", (18, 33),
     dict(question="Q2", exactly_once=True, none_before=True,
          consent="pending_ok"),
     "CORPUS BUG (flagged): the t=32 ratify targets the claim draft, not the "
     "promotion; a Class-3 promotion cannot be 'ratified by the t=33 "
     "distill' under v1.1 consent semantics - compiled as proposed-in-window "
     "and pending-or-accepted by t=33"),
    ("plan", "plan_item_state", (22, 24),
     dict(kinds=("check", "corroborate"), target="F17", min_state="absorbed",
          same_discharge_row=True, provisional_cleared=True), ""),
    ("provenance", "provenance_chain", None,
     dict(findings=("F14", "F19", "F24"), ratified=True),
     "'spans, idle pattern, packet capture, counter data, change record' = "
     "F14/F17/F19/F21-F22/F24; compiled to the three load-bearing links the "
     "ratified claim must carry directly"),
    ("structure", "section_state", None, dict(outline_bounds=True), ""),
])
