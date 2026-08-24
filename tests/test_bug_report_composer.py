"""The bug-report composer had no tests, and it is the only channel a pilot
user has for telling us something is broken.

Reports travel as a `mailto:` URL, so the body is hard-capped (~1050 chars,
~1750 once percent-encoded, under the ~1800 cross-client ceiling). Everything
the composer spends on boilerplate is taken directly from the diagnosis and
the verbatim error — the two things a bugfixer actually needs. In a real
report from the field (2026-08) the fixed overhead was 287 chars, 27% of the
budget, while the error tail was being trimmed to fit.

Three things were wasting it, all measured on that report:
  * the locator repeated the architecture twice and carried the full kernel
    string ("Linux 6.12.0-124.56.1.el10_1.x86_64/x86_64") — 43 chars that are
    identical in every report from a given cluster;
  * `_aba_commit()` shells out to git in the source tree, and a RELEASE
    deploy has no .git, so every real user's report said "unknown" where the
    release id was already known;
  * `_redact` stripped `/Users/<name>` (macOS) but not `/users/<name>` or
    `/home/<name>`, so on Linux clusters — which is all of them — usernames
    and home paths went through verbatim, costing characters and leaking
    identity into an email the user was not shown a diff of.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


@pytest.fixture(scope="module")
def fb():
    from core.graph._schema import init_db
    init_db()
    from content.bio.tools import feedback
    return feedback


def _parts(fb, out):
    body = out["body"]
    locator = body.rsplit("\n", 1)[-1]
    lead = body[:body.index("——") + len("—— your notes above · ABA report below ——\n\n")]
    foot = body[body.index("\nNeed more?"):] if "\nNeed more?" in body else ""
    return body, locator, lead, foot


def test_redacts_linux_home_paths(fb):
    """The cluster case. /Users/ was handled; /users/ and /home/ were not."""
    for raw, why in (("/Users/jane/x.R", "macOS"),
                     ("/users/jane.doe/x.R", "this cluster's layout"),
                     ("/home/jane/x.R", "the ordinary Linux layout")):
        got = fb._redact(f"failed reading {raw}")
        assert "jane" not in got, f"{why}: username survived redaction — {got!r}"
        assert "~" in got, f"{why}: path not replaced — {got!r}"


def test_fixed_overhead_leaves_the_budget_for_content(fb):
    """The boilerplate must not eat a quarter of the report."""
    out = fb.build_bug_report_impl(
        {"headline": "Background R jobs fail immediately",
         "what_doing": "Submitting a background R job.",
         "diagnosis": "The R environment pack is not available.",
         "error_tail": "substrate_offline: compute substrate not configured yet"},
        ctx={"thread_id": "thr_bb88650b", "project_id": "prj_1838135f"})
    body, locator, lead, foot = _parts(fb, out)
    overhead = len(lead) + len(foot)
    assert overhead <= 200, (
        f"fixed overhead {overhead} chars of a {fb.BODY_BUDGET} budget "
        f"({round(100 * overhead / fb.BODY_BUDGET)}%) — that is diagnosis and "
        f"error text the bugfixer does not get.\nlocator({len(locator)}): {locator}")


def test_locator_does_not_repeat_the_architecture(fb):
    """`Linux 6.12.0-…el10_1.x86_64/x86_64` spent 43 chars to say one thing,
    and said the arch twice."""
    out = fb.build_bug_report_impl({"headline": "x"}, ctx={})
    locator = out["body"].rsplit("\n", 1)[-1]
    assert locator.count("x86_64") <= 1 or "aarch64" in locator, \
        f"architecture repeated in the locator: {locator}"
    assert not re.search(r"\d+\.\d+\.\d+-\d+", locator), \
        f"full kernel version string in the locator ({len(locator)} chars): {locator}"


def test_locator_carries_the_release_on_a_release_deploy(fb, monkeypatch):
    """A release deploy has no .git, so the git shell-out returned 'unknown'
    for every real user — while the release id was sitting in config."""
    from core import release as _rel
    monkeypatch.setattr(_rel, "active_release_id", lambda: "2026.08.18-6c1b6783")
    monkeypatch.setattr(fb, "_aba_commit", lambda: "unknown")
    out = fb.build_bug_report_impl({"headline": "x"}, ctx={})
    locator = out["body"].rsplit("\n", 1)[-1]
    assert "2026.08.18-6c1b6783" in locator, \
        f"release id missing; locator says: {locator}"
    assert "unknown" not in locator


def test_subject_clips_on_a_word_boundary(fb):
    """An 80-char hard slice cuts wherever position 80 happens to fall, so the
    subject line — the thing a triager reads first — can end in a word
    fragment. The cut position is FIXED, so a sweep has to move the text past
    it: pad the front by 0..14 chars and every offset must still land clean."""
    import urllib.parse
    base = ("Background R jobs fail immediately with no output whatsoever on "
            "every slurm configured project")
    bad = []
    for pad in range(15):
        headline = ("x" * pad + " " if pad else "") + base
        out = fb.build_bug_report_impl({"headline": headline}, ctx={})
        subject = urllib.parse.unquote_plus(
            out["mailto_url"].split("subject=")[1].split("&")[0])
        assert len(subject) <= 80, subject
        kept = subject.split(": ", 1)[-1].rstrip("…").rstrip()
        if kept == headline:
            continue                              # nothing was clipped
        nxt = headline[len(kept):len(kept) + 1]
        if nxt != " ":
            bad.append(f"pad={pad}: ...{kept[-28:]!r} then {nxt!r}")
    assert not bad, "subject cut mid-word:\n  " + "\n  ".join(bad)
