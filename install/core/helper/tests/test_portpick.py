"""H1 — port discovery."""
import socket
from pathlib import Path

import pytest

from aba_installer import portpick


def _bind(port: int) -> socket.socket:
    """Bind a port to make it unavailable, return the socket so caller can close."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_picks_preferred_when_free(tmp_path):
    p = portpick.pick_port(preferred=43210, state_file=tmp_path / "port.txt")
    assert p == 43210
    assert (tmp_path / "port.txt").read_text().strip() == "43210"


def test_walks_forward_when_preferred_taken(tmp_path):
    s = _bind(43211)
    try:
        p = portpick.pick_port(preferred=43211, state_file=tmp_path / "port.txt")
        assert p == 43212, f"expected 43212 (preferred+1), got {p}"
    finally:
        s.close()


def test_reuses_cached_port_when_still_free(tmp_path):
    state = tmp_path / "port.txt"
    state.write_text("43215")
    p = portpick.pick_port(preferred=43210, state_file=state)
    assert p == 43215, "should re-use cached port over preferred"


def test_falls_back_when_cached_port_now_busy(tmp_path):
    state = tmp_path / "port.txt"
    state.write_text("43216")
    s = _bind(43216)
    try:
        p = portpick.pick_port(preferred=43217, state_file=state)
        assert p == 43217, f"expected fallback to preferred, got {p}"
        assert state.read_text().strip() == "43217"
    finally:
        s.close()


def test_raises_when_window_fully_taken(tmp_path):
    sockets = [_bind(43230 + i) for i in range(4)]
    try:
        with pytest.raises(RuntimeError):
            portpick.pick_port(preferred=43230, tries=4, state_file=tmp_path / "p")
    finally:
        for s in sockets:
            s.close()
