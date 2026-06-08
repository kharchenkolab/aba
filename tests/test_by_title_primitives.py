"""R1 — slug + atomic-symlink primitives.

Standalone module tests; no ABA backend / sqlite required.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.by_title import (  # noqa: E402
    slugify, pick_slug, atomic_symlink, clear_symlink, existing_slugs_in,
)


# ─── slugify ────────────────────────────────────────────────────────────
def test_slugify_basic():
    assert slugify("My scRNA project") == "My-scRNA-project"
    assert slugify("QC violins v2") == "QC-violins-v2"


def test_slugify_strips_punctuation():
    assert slugify("Élena's plot!") == "Elena-s-plot"
    assert slugify("UMAP / clusters") == "UMAP-clusters"
    assert slugify("a@b#c$d%e") == "a-b-c-d-e"


def test_slugify_collapses_runs():
    assert slugify("  hello   world  ") == "hello-world"
    assert slugify("---a---b---") == "a-b"
    assert slugify("a___b") == "a-b"


def test_slugify_idempotent():
    s1 = slugify("UMAP — clusters 1–7")
    assert slugify(s1) == s1


def test_slugify_falls_back_on_empty():
    assert slugify("") == "untitled"
    assert slugify("!!!") == "untitled"
    assert slugify("   ") == "untitled"


def test_slugify_truncates_long_titles():
    long = "x" * 200
    s = slugify(long)
    assert len(s) <= 80


def test_slugify_handles_non_latin_gracefully():
    """CJK strings fold to nothing under NFKD→ASCII; we fall back."""
    assert slugify("中文标题") == "untitled"


def test_slugify_preserves_underscores_and_dots():
    # Dots are filesystem-safe and useful for file extensions; underscores
    # too but we collapse runs.
    assert slugify("my.file.v2") == "my.file.v2"
    assert slugify("foo_bar.txt") == "foo-bar.txt"


# ─── pick_slug ──────────────────────────────────────────────────────────
def test_pick_slug_returns_base_when_free():
    assert pick_slug("UMAP plot", taken=set()) == "UMAP-plot"


def test_pick_slug_appends_short_id_on_collision():
    taken = {"UMAP-plot"}
    out = pick_slug("UMAP plot", taken=taken, fallback_id="fig_abc1234567")
    # short = last 6 of "fig_abc1234567" = "234567"
    assert out == "UMAP-plot_234567"


def test_pick_slug_deterministic_with_fallback_id():
    """Same input → same output (so a given entity always lands at the
    same slug across re-runs)."""
    a = pick_slug("UMAP", taken={"UMAP"}, fallback_id="fig_abc123def4")
    b = pick_slug("UMAP", taken={"UMAP"}, fallback_id="fig_abc123def4")
    assert a == b


def test_pick_slug_counter_fallback_on_double_collision():
    """Both base AND suffixed slug taken — append numeric."""
    taken = {"UMAP", "UMAP_abc123"}
    out = pick_slug("UMAP", taken=taken, fallback_id="fig_xxxxabc123")
    # short = last 6 = "abc123" → "UMAP_abc123" is taken → "UMAP_abc123_2"
    assert out == "UMAP_abc123_2"


def test_pick_slug_random_short_when_no_fallback_id():
    """Without fallback_id we still resolve, just non-deterministically."""
    taken = {"UMAP"}
    out = pick_slug("UMAP", taken=taken)
    assert out.startswith("UMAP_")
    assert out != "UMAP"


# ─── atomic_symlink ─────────────────────────────────────────────────────
def test_atomic_symlink_creates_link(tmp_path):
    target = tmp_path / "real-file.txt"
    target.write_text("hello")
    link = tmp_path / "link.txt"
    atomic_symlink(target, link)
    assert link.is_symlink()
    assert link.read_text() == "hello"


def test_atomic_symlink_replaces_existing(tmp_path):
    a = tmp_path / "a.txt"; a.write_text("A")
    b = tmp_path / "b.txt"; b.write_text("B")
    link = tmp_path / "link.txt"
    atomic_symlink(a, link)
    atomic_symlink(b, link)
    assert link.read_text() == "B"


def test_atomic_symlink_creates_parent_dirs(tmp_path):
    target = tmp_path / "target.txt"; target.write_text("x")
    link = tmp_path / "deep" / "nested" / "link.txt"
    atomic_symlink(target, link)
    assert link.is_symlink()


def test_atomic_symlink_relative_target_text_preserved(tmp_path):
    """The symlink's target string is written verbatim, not resolved.
    Critical for portability: relative targets must STAY relative."""
    (tmp_path / "src" / "real.txt").parent.mkdir()
    (tmp_path / "src" / "real.txt").write_text("ok")
    link = tmp_path / "links" / "link.txt"
    atomic_symlink("../src/real.txt", link)
    assert os.readlink(link) == "../src/real.txt"
    assert link.read_text() == "ok"


def test_atomic_symlink_tempfile_cleaned_on_failure(tmp_path, monkeypatch):
    """If os.replace fails, the tempfile must not be left behind."""
    target = tmp_path / "t.txt"; target.write_text("x")
    link = tmp_path / "link.txt"
    # Replace os.replace with one that raises
    orig = os.replace
    def boom(*a, **k): raise OSError("simulated")
    monkeypatch.setattr(os, "replace", boom)
    try:
        atomic_symlink(target, link)
    except OSError:
        pass
    monkeypatch.setattr(os, "replace", orig)
    # No tempfile residue
    residue = [p for p in tmp_path.iterdir() if p.name.startswith(".link.txt.tmp-")]
    assert residue == [], f"tempfile not cleaned: {residue}"


# ─── clear_symlink ──────────────────────────────────────────────────────
def test_clear_symlink_removes_symlink(tmp_path):
    target = tmp_path / "t.txt"; target.write_text("x")
    link = tmp_path / "link.txt"
    atomic_symlink(target, link)
    assert clear_symlink(link) is True
    assert not link.exists()


def test_clear_symlink_idempotent_when_missing(tmp_path):
    assert clear_symlink(tmp_path / "nothing") is False


def test_clear_symlink_refuses_regular_file(tmp_path):
    """Critical: never deletes a real file. The slug picker collision-suffixes
    around real files, so the symlink should have a different name — but be
    defensive in case a user manually swapped one in."""
    f = tmp_path / "real-file.txt"
    f.write_text("user content")
    assert clear_symlink(f) is False
    assert f.exists()
    assert f.read_text() == "user content"


# ─── existing_slugs_in ──────────────────────────────────────────────────
def test_existing_slugs_in_returns_symlinks_only(tmp_path):
    target = tmp_path / "x.txt"; target.write_text("x")
    atomic_symlink(target, tmp_path / "linkA")
    atomic_symlink(target, tmp_path / "linkB")
    (tmp_path / "real-file.txt").write_text("real")
    assert existing_slugs_in(tmp_path) == {"linkA", "linkB"}


def test_existing_slugs_in_missing_dir(tmp_path):
    assert existing_slugs_in(tmp_path / "missing") == set()


# ─── runner ─────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            # Tests that take tmp_path get one ad-hoc
            sig = fn.__code__.co_varnames[:fn.__code__.co_argcount]
            if "tmp_path" in sig or "monkeypatch" in sig:
                # Minimal pytest-style runner: spin a tempdir + a fake monkeypatch
                td = Path(tempfile.mkdtemp(prefix="aba_bytitle_t_"))
                class _MP:
                    def __init__(self): self._restore = []
                    def setattr(self, target, name_or_value, value=None):
                        if value is None:
                            value = name_or_value
                            # target is "module.attr" string OR (obj, name)
                            raise NotImplementedError
                        else:
                            old = getattr(target, name_or_value)
                            self._restore.append((target, name_or_value, old))
                            setattr(target, name_or_value, value)
                    def undo(self):
                        for t, n, v in reversed(self._restore):
                            setattr(t, n, v)
                mp = _MP()
                kwargs = {}
                if "tmp_path" in sig: kwargs["tmp_path"] = td
                if "monkeypatch" in sig: kwargs["monkeypatch"] = mp
                try:
                    fn(**kwargs)
                finally:
                    mp.undo()
            else:
                fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
