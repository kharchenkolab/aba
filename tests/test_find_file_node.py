"""find_file_node (content/bio/files/tree.py) — tolerant file resolution so a
bare basename / partial path resolves to the real tree node (open_viewer + the
viewer-launch endpoints rely on this)."""
from content.bio.files.tree import find_file_node, find_node, list_file_matches


TREE = {
    "kind": "root", "name": "", "path": "", "children": [
        {"kind": "folder", "name": "a", "path": "a", "children": [
            {"kind": "file", "name": "processed.h5ad", "path": "a/out/processed.h5ad", "mtime": 100},
        ]},
        {"kind": "folder", "name": "b", "path": "b", "children": [
            {"kind": "file", "name": "processed.h5ad", "path": "b/run/processed.h5ad", "mtime": 300},
            {"kind": "file", "name": "raw.h5ad", "path": "b/raw.h5ad", "mtime": 50},
        ]},
    ],
}


def test_exact_path():
    assert find_file_node(TREE, "b/raw.h5ad")["path"] == "b/raw.h5ad"


def test_bare_basename():
    # unique-ish basename resolves; 'raw.h5ad' only in one place
    assert find_file_node(TREE, "raw.h5ad")["path"] == "b/raw.h5ad"


def test_partial_suffix_path():
    assert find_file_node(TREE, "out/processed.h5ad")["path"] == "a/out/processed.h5ad"


def test_ambiguous_basename_prefers_newest():
    # 'processed.h5ad' exists twice → prefer the most recently modified (mtime 300)
    assert find_file_node(TREE, "processed.h5ad")["path"] == "b/run/processed.h5ad"


def test_not_found():
    assert find_file_node(TREE, "nope.h5ad") is None


def test_list_matches_for_did_you_mean():
    assert set(list_file_matches(TREE, "processed.h5ad")) == {
        "a/out/processed.h5ad", "b/run/processed.h5ad"}


def test_find_node_still_exact_only():
    # the strict resolver is unchanged — a basename does NOT match it
    assert find_node(TREE, "raw.h5ad") is None
    assert find_node(TREE, "b/raw.h5ad")["path"] == "b/raw.h5ad"
