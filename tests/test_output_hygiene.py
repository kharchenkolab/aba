"""P3 hygiene guards: terminal-redraw collapsing, and the isolated-env handle."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
pytestmark = pytest.mark.platform
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.output_cap import collapse_progress, snip_middle  # noqa: E402


def test_carriage_return_frames_collapse_to_the_last():
    """A progress bar repaints one line thousands of times. Only the final
    frame is information; the rest is repaint noise that character-capping
    cannot distinguish (live: ~4 KB of bar frames entered a tool result)."""
    bar = "".join(f"\rEpoch {i}/100 [{'#' * (i // 10)}]" for i in range(1, 101))
    got = collapse_progress(bar)
    assert got == "Epoch 100/100 [##########]"
    assert "\r" not in got


def test_ansi_escapes_are_removed():
    assert collapse_progress("\x1b[32mok\x1b[0m") == "ok"
    assert collapse_progress("\x1b[2K\x1b[1Gtidy") == "tidy"


def test_real_newlines_and_plain_text_survive_untouched():
    """CEILING: ordinary multi-line output must be byte-identical — this must
    not become a general text mangler."""
    plain = "row 1\nrow 2\nrow 3\n"
    assert collapse_progress(plain) == plain
    assert collapse_progress("") == ""
    assert collapse_progress("no control chars here") == "no control chars here"


def test_mixed_progress_and_real_output():
    text = "loading\n\rstep 1\rstep 2\rstep 3\ndone\n"
    assert collapse_progress(text) == "loading\nstep 3\ndone\n"


def test_snip_measures_information_not_repaint_frames():
    """ARMED: a bar longer than the cap plus a short real result. Without
    collapsing, the bar eats the budget and the real output lands in the
    snipped middle; with it, nothing needs snipping at all."""
    bar = "".join(f"\r[{i:04d}/2000]" for i in range(2000))
    text = bar + "\nRESULT: 42\n"
    out = snip_middle(text, cap=200)
    assert "RESULT: 42" in out
    assert "ABA snipped" not in out, "collapsed output fits the cap; no snip needed"


def test_snip_still_caps_genuinely_long_output():
    """The other side: real information past the cap must STILL be snipped."""
    out = snip_middle("x" * 5000, cap=200)
    assert "ABA snipped" in out and len(out) < 5000
