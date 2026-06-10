"""Tier-0-tray — macOS menu-bar app on top of the helper service.

A clickable ``Applications/ABA.app`` + a persistent menu-bar item with
status / start / stop / restart / open / check-updates, all reading off
the helper's existing loopback API. No new backend code; the tray is a
client surface that lives in the same helper venv.

See ``misc/mac-install.md`` § 3c for the design rationale (why rumps over
Swift, why we layer on top of Tier-0b rather than replace it, what we
mitigate vs Tier 3)."""
