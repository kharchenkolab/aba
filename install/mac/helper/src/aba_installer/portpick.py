"""Pick a free port for the helper's HTTP service.

Starts at the preferred port, walks forward up to `tries` ports. Persists
the chosen port to a file so subsequent restarts re-use it (browser
bookmarks, LaunchAgent invocations stay consistent).
"""
from __future__ import annotations
import socket
from pathlib import Path
from typing import Optional


DEFAULT_PORT = 8765


def is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True if the TCP port is bindable on loopback. Loopback-only —
    avoids triggering macOS's incoming-connections firewall prompt."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(preferred: int = DEFAULT_PORT, *, tries: int = 16,
              state_file: Optional[Path] = None) -> int:
    """Return a free port. If state_file exists and its port is still free,
    use it (sticky). Otherwise walk from `preferred` up to `preferred+tries`."""
    if state_file is not None and state_file.exists():
        try:
            cached = int(state_file.read_text().strip())
            if is_free(cached):
                return cached
        except (ValueError, OSError):
            pass

    for offset in range(tries):
        candidate = preferred + offset
        if is_free(candidate):
            if state_file is not None:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                state_file.write_text(str(candidate))
            return candidate
    raise RuntimeError(f"no free port in [{preferred}, {preferred + tries})")
