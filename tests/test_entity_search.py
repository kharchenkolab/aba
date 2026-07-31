"""Entity title search: tokenized, not one contiguous LIKE.

The old filter was a single `lower(title) LIKE %query%`, so any multi-word
phrasing failed against a title with punctuation between the words. Live
consequence (2026-07-26): two empty results for a dataset that WAS listed in
the agent's own orientation banner convinced it the entity did not exist, and
it deleted the backing file with raw `os.remove` in a remote code block instead
of using the entity verbs.

ARMED: the multi-token tests are built on a title where the tokens are
NON-contiguous, which is exactly the shape the old query could not match — a
substring-only implementation fails them. WIDE: covers the no-query path
(unchanged), the limit/ranking interaction (a limit must not truncate away the
best match), token order independence, and the honest empty result.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_TMP = tempfile.mkdtemp(prefix="aba_esearch_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "e.db")
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.graph import entities as E  # noqa: E402
from core.graph import _schema  # noqa: E402

_schema.init_db()

# The live shape: tokens separated by punctuation, never contiguous.
TITLES = [
    "processed-86c9ac87.lstar.zarr",
    "integrated_counts-0145fe78.store",
    "qc summary table",
    "figure_umap_by_sample.png",
]


@pytest.fixture(scope="module", autouse=True)
def _seed():
    for t in TITLES:
        E.create_entity(entity_type="dataset", title=t)


def _titles(**kw):
    return [e["title"] for e in E.list_entities(**kw)]


def test_multi_token_matches_non_contiguous_title():
    """THE regression: the old contiguous LIKE returned nothing for this."""
    got = _titles(title_query="processed zarr")
    assert got == ["processed-86c9ac87.lstar.zarr"], got
    got = _titles(title_query="integrated store")
    assert got == ["integrated_counts-0145fe78.store"], got


def test_token_order_does_not_matter():
    assert _titles(title_query="zarr processed") == ["processed-86c9ac87.lstar.zarr"]
    assert _titles(title_query="table summary qc") == ["qc summary table"]


def test_single_token_still_works_as_a_substring():
    """Ceiling: the simple case must not regress."""
    assert _titles(title_query="lstar") == ["processed-86c9ac87.lstar.zarr"]
    assert _titles(title_query=".png") == ["figure_umap_by_sample.png"]


def test_all_tokens_are_required():
    """AND, not OR — an OR would flood the caller with near-misses."""
    assert _titles(title_query="processed nonexistenttoken") == []


def test_contiguous_phrase_ranks_first():
    E.create_entity(entity_type="dataset", title="qc summary of the table")   # tokens split
    got = _titles(title_query="summary table")
    assert len(got) == 2
    assert got[0] == "qc summary table", f"verbatim phrase must rank first: {got}"


def test_limit_is_applied_after_ranking(monkeypatch):
    """A limit must not truncate away the best match. With the limit pushed
    into SQL, ordering was by creation time, so the verbatim phrase could be
    cut and the caller would see only the weaker hit."""
    got = _titles(title_query="summary table", limit=1)
    assert got == ["qc summary table"], got


def test_empty_query_path_is_unchanged():
    """No query → the original SQL path: every entity, pinned-then-created
    order, SQL-side limit."""
    all_titles = _titles()
    assert len(all_titles) >= len(TITLES)
    assert _titles(limit=2) == all_titles[:2]


def test_whitespace_only_query_is_not_a_filter():
    """WIDE — the degenerate input: '   ' must behave as "no query", not as a
    token that matches nothing."""
    assert len(_titles(title_query="   ")) == len(_titles())


def test_type_filter_composes_with_the_query():
    E.create_entity(entity_type="figure", title="processed overview zarr",
                    artifact_path="figs/scatter.png")
    ds = _titles(title_query="processed zarr", type_filter="dataset")
    fig = _titles(title_query="processed zarr", type_filter="figure")
    assert ds == ["processed-86c9ac87.lstar.zarr"]
    assert fig == ["processed overview zarr"]
