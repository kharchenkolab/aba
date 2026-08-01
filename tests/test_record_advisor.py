"""Record drafting advisor (content/bio/record_advisor.py) — phase-3 slice.

Loads the advisor module STANDALONE (spec_from_file_location, so the guard
stays hermetic — no content-pack import chain) over a seeded generic DB and
asserts the detector's contract:

  armed:  the seeds exist before any assertion reads the proposal store;
  fires:  >=2 claims + no narrative -> exactly one pending record_draft;
  dedup:  a second review at the same claim count is a no-op (signature);
  moves:  a third claim re-arms it (new signature = the world changed);
  quiet:  a thread WITH narrative, or with <2 claims, never fires.

Run: python tests/test_record_advisor.py   (or pytest)
"""
from __future__ import annotations
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_record_advisor_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from core.graph._schema import init_db                     # noqa: E402
from core.graph.entities import create_entity              # noqa: E402
from core.graph.proposals_store import list_proposals      # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "record_advisor_standalone",
    _BACKEND / "content" / "bio" / "record_advisor.py")
advisor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(advisor)


def _claim(tid: str, title: str) -> str:
    return create_entity(entity_type="claim", title=title,
                         metadata={"thread_id": tid, "confidence": "preliminary"})


class RecordAdvisorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.t1 = create_entity(entity_type="thread", title="line one",
                               metadata={"question": "what drives variance?"})
        cls.t2 = create_entity(entity_type="thread", title="line two",
                               metadata={"question": "where is the ceiling?"})

    def test_advisor_lifecycle(self):
        # quiet below threshold
        _claim(self.t1, "c1")
        self.assertIsNone(advisor.review_thread(self.t1))
        # armed: two claims, no narrative — fires exactly once
        _claim(self.t1, "c2")
        pid = advisor.review_thread(self.t1)
        self.assertIsNotNone(pid)
        rows = [p for p in list_proposals(thread_id=self.t1)
                if p["kind"] == "record_draft"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["advisor"], "record_drafter")
        # the proposal carries the DRAFTED PROSE — a story, not a bare stub
        text = (rows[0].get("payload") or {}).get("text", "")
        self.assertIn("c1", text)
        self.assertIn("c2", text)
        self.assertIn("(preliminary)", text)
        # dedup: same world state, no re-nag
        self.assertIsNone(advisor.review_thread(self.t1))
        # the world changes (third claim) — a NEW signature may fire
        _claim(self.t1, "c3")
        self.assertIsNotNone(advisor.review_thread(self.t1))
        # quiet when a HAND-WRITTEN narrative exists (no drafted_claims
        # marker -> the advisor never second-guesses human prose)
        _claim(self.t2, "d1"); _claim(self.t2, "d2")
        create_entity(entity_type="narrative", title="already drafted",
                      metadata={"thread_id": self.t2})
        self.assertIsNone(advisor.review_thread(self.t2))

    def test_revision_lifecycle(self):
        from core.graph.edges import add_edge
        t3 = create_entity(entity_type="thread", title="line three",
                           metadata={"question": "what sets the floor?"})
        _claim(t3, "e1"); _claim(t3, "e2")
        # a drafted head at 2 claims (as the scheduler would write it)
        old = create_entity(entity_type="narrative", title="What we know",
                            metadata={"thread_id": t3, "text": "two things",
                                      "drafted_claims": 2})
        self.assertIsNone(advisor.review_thread(t3))     # not stale
        _claim(t3, "e3")                                 # the world moved
        pid = advisor.review_thread(t3)
        self.assertIsNotNone(pid)
        row = [p for p in list_proposals(thread_id=t3)
               if p["kind"] == "record_draft"][0]
        self.assertEqual(row["payload"]["revises"], old)
        self.assertEqual(row["payload"]["drafted_claims"], 3)
        self.assertIn("revise", row["headline"])
        self.assertIn("e3", row["payload"]["text"])
        # simulate accept: revision + provenance edge -> head moves, quiet
        new = create_entity(entity_type="narrative", title="What we know",
                            metadata={"thread_id": t3, "text": "three things",
                                      "drafted_claims": 3})
        add_edge(new, old, "wasDerivedFrom")
        self.assertEqual([h["id"] for h in advisor._heads(
            advisor._of_thread("narrative", t3))], [new])
        self.assertIsNone(advisor.review_thread(t3))

    def test_llm_draft_gate_and_flag(self):
        mk = lambda t, conf: {"title": t, "metadata": {"confidence": conf}}
        cs = [mk("a finding", "supported"), mk("a hunch", "preliminary")]
        # flag off -> NO network path, empty string (deterministic backstop)
        os.environ.pop("RECORD_LLM_DRAFTS", None)
        self.assertEqual(advisor.llm_draft(cs), "")
        # the mechanical gate: plain prose naming a maturity passes; lists,
        # maturity-free prose, and id leaks are rejected whole
        ok = "A finding holds (supported); a hunch remains (preliminary)."
        self.assertEqual(advisor._gate_draft(ok, cs), ok)
        self.assertEqual(advisor._gate_draft("- a finding (supported)", cs), "")
        self.assertEqual(advisor._gate_draft("A finding holds.", cs), "")
        self.assertEqual(
            advisor._gate_draft("A finding (supported) via thr_ab12.", cs), "")
        self.assertEqual(advisor._gate_draft("", cs), "")

    def test_question_distillation(self):
        os.environ.pop("RECORD_LLM_DRAFTS", None)
        blob = ("I have been watching the batch dashboards since the deploy "
                "and the numbers drift apart every afternoon while mornings "
                "look fine. What actually drives the afternoon divergence "
                "at siteA? Also the weekly report never flagged it.")
        t = create_entity(entity_type="thread", title="afternoon drift",
                          metadata={"question": blob})
        pid = advisor.distill_question(t)
        self.assertIsNotNone(pid)
        row = [p for p in list_proposals(thread_id=t)
               if p["kind"] == "question"][0]
        self.assertEqual(row["payload"]["question"],
                         "what actually drives the afternoon divergence "
                         "at siteA?")
        self.assertEqual(row["payload"]["set_source"], "guide")
        # dedup: same verbatim question, no re-nag
        self.assertIsNone(advisor.distill_question(t))
        # crisp questions stay untouched; distilled ones never re-nag
        t2 = create_entity(entity_type="thread", title="crisp",
                           metadata={"question": "where is the ceiling?"})
        self.assertIsNone(advisor.distill_question(t2))
        t3 = create_entity(entity_type="thread", title="already done",
                           metadata={"question": "x" * 200,
                                     "question_source": "guide"})
        self.assertIsNone(advisor.distill_question(t3))
        # no question mark anywhere -> nothing trustworthy to propose
        t4 = create_entity(entity_type="thread", title="statement",
                           metadata={"question": "y" * 200})
        self.assertIsNone(advisor.distill_question(t4))

    def test_compose_draft_reads_strongest_first_negatives_apart(self):
        mk = lambda t, conf: {"title": t, "metadata": {"confidence": conf}}
        text = advisor.compose_draft([
            mk("early hunch", "preliminary"),
            mk("the load-bearing result", "validated"),
            mk("ruled out path", "refuted"),
            mk("a middling read", "supported"),
        ])
        # leads with the strongest positive, sets negatives apart, and the
        # whole thing reads as sentences — no ids, no raw structures
        self.assertTrue(text.startswith("the load-bearing result (validated)."))
        self.assertIn("Also in hand: a middling read (supported); "
                      "early hunch (preliminary).", text)
        self.assertIn("Set aside: ruled out path (refuted).", text)
        self.assertNotIn("thr_", text)
        self.assertNotIn("{", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
