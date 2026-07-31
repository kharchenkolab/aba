"""Compiled assertions — pool gaia-bh1 (54)."""

from . import _mk

P = "gaia-bh1"
COMPILED = []

COMPILED += _mk(P, "absence", [
    ("structure", "routing_destination", (14, 17),
     dict(spec={"F16": dict(allowed=("sediment",)),
                "F27": dict(allowed=("sediment",)),
                "F28": dict(allowed=("sediment",))}),
     "'refresh numbers at Class 0/1 only' folds into sediment-only routing "
     "of the background landings; no story prose from them"),
    ("structure", "gap_record", (19, 19),
     dict(min_gap_days=24, briefing=True),
     "briefing content ('robustness confirmation leads, merge decision "
     "flagged') is prose quality; presence + gap length checkable"),
    ("consent", "proposal_state", (19, 19),
     dict(desc_tokens=("merge", "subsections", "confirmation"), cls="2",
          status="pending", max_active_age=13), ""),
    ("structure", "ops_bounded", (13, 19),
     dict(structure_frozen=True),
     "window widened to cover the whole gap through re-entry: 'exactly as "
     "left at departure' = no structural op from the first gap clock to t=19"),
    ("plan", "plan_item_state", (20, 25),
     dict(owner="Q4", min_state="produced", distinct_count=2),
     "'channel-modeling item still open at the end' is a third-item check "
     "folded into the not-all-absorbed reading; primary: two Q4 items "
     "reach produced as F26/F32 land"),
    ("provenance", "gap_record", None,
     dict(min_gap_days=24, background_landings=3,
          check_touched_last_sitting=True), ""),
])

COMPILED += _mk(P, "busy-scientist", [
    ("plan", "plan_item_state", (8, 8),
     dict(kinds=("check",), target="F03", min_state="planned",
          provisional_target=True), ""),
    ("structure", "sitting_index", (15, 20), dict(mode="coalesce"), ""),
    ("consent", "proposal_state", (21, 22),
     dict(kind="claim_draft", desc_tokens=("F12",), status="pending",
          created_in_window=True), ""),
    ("salience", "proposal_state", (21, 22),
     dict(kind="claim_draft", desc_tokens=("F12",), status="pending",
          requires_provisional_dep="F03"),
     "MODEL GAP (flagged): provisional-mark inheritance along depends_on is "
     "not first-class; compiled as the draft pending while F03's check item "
     "keeps its provisional mark open"),
    ("consent", "proposal_state", None,
     dict(kind="claim_draft", desc_tokens=("F12",), status="pending"), ""),
    ("routing", "tray_state", None,
     dict(rows_for=("F02", "F03", "F43", "F39")),
     "'by place, not per-episode' compiled as: a visible destination row "
     "exists for every gesture-named finding - see the checkout sibling note"),
    ("structure", "salience_state", None,
     dict(specs=[("F04", "hold_evaporated"), ("F02", "pinned"),
                 ("F43", "faded_findable")]), ""),
    ("salience", "stub_is_plan", None,
     dict(question="Q5", intent=True, no_prose=True), ""),
])

COMPILED += _mk(P, "contradiction", [
    ("provenance", "overturn_handling", (9, 10),
     dict(pairs=[("F07", "F10")], mode="absorb"),
     "'no prior warning in this ordering' is a stream fact (F08 arrives "
     "after); the checkable part is provenance-preserving absorption"),
    ("provenance", "overturn_handling", (13, 15),
     dict(pairs=[("F03", "F12")], mode="absorb"),
     "'no prose still cites the superseded numbers' is numeric content; "
     "compiled as citations-revised supersession"),
    ("structure", "overturn_handling", (26, 26),
     dict(pairs=[("F32", "F35")], mode="interrupt"), ""),
    ("provenance", "overturn_handling", (26, 26),
     dict(pairs=[("F03", "F12"), ("F32", "F35")], mode="differential"), ""),
    ("provenance", "ops_bounded", (27, 31),
     dict(no_revise_ratified=True,
          forbid_new_addendum_except=("F35", "F32", "ce", "common-envelope")),
     "'absorb as corroboration, not fresh contradictions' compiled as: no "
     "ratified-prose rewrite and no addendum proposals beyond the F35/CE "
     "revision itself in the window"),
    ("plan", "plan_item_state", (27, 31),
     dict(kinds=("alternatives",), target="F32", min_state="absorbed"), ""),
    ("consent", "consent_conservation", None,
     dict(no_auto_ratify=True, addenda_via_proposals=True), ""),
    ("structure", "provenance_chain", None,
     dict(findings=("F32", "F35", "F33", "F34"), via_section=True,
          question="Q4"),
     "'reads as CE-ruled-out' is prose content; compiled as the revision "
     "chain being reachable from the Q4 section"),
])

COMPILED += _mk(P, "flood", [
    ("structure", "tray_state", (25, 31),
     dict(non_empty=True, min_routine=1, max_undifferentiated=9),
     "peak-sampled over the window: the t=31 distill settles the rows, so "
     "the end snapshot is empty for every policy"),
    ("structure", "sitting_index", (31, 31), dict(mode="mid_distill", at=31), ""),
    ("routing", "routing_destination", (36, 39),
     dict(spec={"F43": dict(allowed=("notes", "sediment"), faded=True),
                "F44": dict(allowed=("notes", "sediment"), row_exists=True)}), ""),
    ("structure", "narrative_growth_bounded", (24, 39),
     dict(max_changes=3, min_changes=1),
     "min_changes=1 per the corpus's own V2 fix intent (growth must not be "
     "zero) - kills the inert vacuous pass"),
    ("structure", "proposal_state", None,
     dict(kind="claim_draft", any_of_desc=(("F24",), ("F18",))),
     "pending or ratified both acceptable: status left at default 'pending'; "
     "an accepted draft also satisfies via the any-status fallback below"),
    ("provenance", "sitting_index", None,
     dict(mode="leftovers", require_hold_evaporated="F20"),
     "'at minimum artifacts of F19, F21, F22' relaxed: leftovers content "
     "depends on what the policy routed; checked as shelf-plus-hold "
     "semantics (the routed-set varies per policy)"),
    ("consent", "consent_conservation", None,
     dict(no_auto_ratify=True, hold_evaporated="F20"), ""),
])

COMPILED += _mk(P, "interleaved", [
    ("routing", "cited_under_questions", (21, 23),
     dict(findings=("F14",), questions=("Q1", "Q3"), forbidden=("Q2",)), ""),
    ("routing", "cited_under_questions", (26, 28),
     dict(findings=("F20",), questions=("Q2", "Q3", "Q4")),
     "the distinct Q4-vs-Q3 framing of the citation is prose content"),
    ("consent", "routing_destination", (15, 19),
     dict(spec={"F23": dict(require_row_question="Q1")}),
     "'visible proposals or receipts' compiled as a routing row placing the "
     "Q3-sitting finding under Q1 - the row is the receipt"),
    ("structure", "section_state", None,
     dict(question="Q2", min_cited=2),
     "'Q2..Q5 in parallel, no duplicated prose' proxied by Q2 citation mass "
     "plus the no-copies discipline in assertions 0-1 (Q3/Q4 covered there)"),
    ("routing", "cited_under_questions", None, dict(all_tagged=True), ""),
    ("plan", "plan_item_state", None,
     dict(kinds=("corroborate",), target="F23", min_state="absorbed"), ""),
])

COMPILED += _mk(P, "pivot", [
    ("structure", "section_state", (34, 37),
     dict(question="Q1", min_cited=8), ""),
    ("consent", "proposal_state", (38, 40),
     dict(desc_tokens=("Q1", "closure", "appendix", "restructuring"), cls="3",
          status="accepted", accepted_at_t=39, applied=True),
     "'reader impact stated' is proposal prose; compiled as the Class-3 "
     "batch accepted exactly at t=39 and applied"),
    ("structure", "section_state", (42, 47),
     dict(question="Q1", status="closed", any_appendix=True,
          nothing_deleted=True), ""),
    ("routing", "cited_under_questions", (42, 55),
     dict(question_map={"F20": ("Q4",), "F32": ("Q4",), "F35": ("Q4",),
                        "F37": ("Q4",), "F39": ("Q5",), "F40": ("Q5",),
                        "F41": ("Q5",), "F42": ("Q5",), "F45": ("Q5",)},
          forbidden=("Q1",)),
     "F20 is triple-tagged (Q2/Q3/Q4); the pivot assertion names its Q4 "
     "routing, so only Q4 membership is required here"),
    ("provenance", "provenance_chain", None,
     dict(findings=("F29", "F30"), via_section=True, question="Q1"), ""),
    ("structure", "tray_state", None,
     dict(compare_around_t=40),
     "'needs-you count unchanged by the restructuring itself' compiled as: "
     "tray delta across the Class-3 application bounded by the items the "
     "pivot actually resolved"),
])

COMPILED += _mk(P, "proactive-intent", [
    ("structure", "stub_is_plan", (10, 11),
     dict(question="Q4", intent=True, no_prose=True), ""),
    ("plan", "plan_item_state", (10, 11),
     dict(owner="Q4", distinct_count=2, min_state="planned"), ""),
    ("salience", "stub_is_plan", (16, 18),
     dict(question="Q4", intent=True, no_prose=True),
     "floor conversion: the stub still carries no prose after the "
     "emphasize-now instruction; 'refusal stated in evidence terms' is "
     "response prose the trace cannot grade"),
    ("plan", "plan_item_state", (20, 24),
     dict(owner="Q4", min_state="produced", distinct_count=1),
     "'evidence counter advances' is a rendering; the lifecycle transition "
     "is the checkable part"),
    ("structure", "section_state", None,
     dict(question="Q4", intent=True, min_cited=2),
     "'sub-directions without evidence remain future-tense' is per-child "
     "rendering; compiled as intent-marked Q4 grown to cited evidence"),
    ("consent", "ops_bounded", (12, 999),
     dict(max_cls="2"),
     "MODEL GAP (same as checkout sibling): standing pre-consent is not "
     "first-class; compiled as nothing above Class 2 applied after the "
     "declaration"),
])

COMPILED += _mk(P, "slow-burn", [
    ("structure", "section_state", (24, 31),
     dict(question="Q4", no_child_section=True), ""),
    ("structure", "section_promotion", (33, 42),
     dict(question="Q4", exactly_once=True, none_before=True,
          consent="accepted"),
     "corpus v1.2: the explicit ratify at t=42 consents the Class-3 "
     "promotion; strict reading"),
    ("salience", "plan_item_state", (21, 22),
     dict(targets_any=("F21", "F22"), min_state="planned",
          forbid_prose_from=("F21", "F22"), max_prose_maturity="conjecture"),
     "floor conversion: planned corroboration items exist for the age story "
     "and no prose from the weak findings exceeds conjecture grade"),
    ("structure", "section_state", None, dict(outline_bounds=True), ""),
    ("provenance", "overturn_handling", None,
     dict(pairs=[("F32", "F35")], mode="interrupt",
          post_still_cited=("F33", "F34")), ""),
    ("plan", "plan_item_state", None,
     dict(kinds=("plan",), target="F26", min_state="taken-up",
          not_dangling=True),
     "the plan gesture carries no text (schema gap, REPORT §5.2): existence "
     "+ lifecycle checked, content not"),
    ("consent", "consent_conservation", None,
     dict(no_auto_ratify=True),
     "corpus v1.2: promotion-specific consent is checked strictly in "
     "assertion 1; this cell keeps the general conservation check"),
])
