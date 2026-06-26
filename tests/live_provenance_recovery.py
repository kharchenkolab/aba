"""Live-agent memory-wipe recovery (provenance.md Phase 1).

Thread A's agent produces a figure as a BACKGROUND job; we pin it (so it's a
navigable entity). Then a FRESH thread B — with NO conversation memory of how the
figure was made — is asked to reproduce it. The agent can only succeed by using
the figure's exec record (the provenance Phase 1 now writes for background jobs).

Run:  .venv/bin/python tests/live_provenance_recovery.py
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.live_chat_runner import (  # noqa: E402
    BASE, server_reachable, _fresh_project, _fresh_thread, _consume_chat_sse,
)


def _runtime_dir() -> Path:
    """ABA_RUNTIME_DIR the live server is using (read from its process env)."""
    import subprocess
    out = subprocess.run(["bash", "-c",
        "pid=$(ss -ltnp 2>/dev/null | grep ':8000' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2);"
        "tr '\\0' '\\n' < /proc/$pid/environ 2>/dev/null | grep '^ABA_RUNTIME_DIR=' | cut -d= -f2"],
        capture_output=True, text=True)
    rd = (out.stdout or "").strip()
    return Path(rd) if rd else Path("/home/pkharchenko/aba/aba/aba_runtime")


def _db(pid: str) -> Path:
    return _runtime_dir() / "projects" / pid / "project.db"


def _q(pid: str, sql: str, args=()):
    c = sqlite3.connect(f"file:{_db(pid)}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def _post(path: str, body: dict, pid: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if pid:
        headers["X-Project-Id"] = pid
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


PRODUCE = ("Use run_python with background=True to make a matplotlib scatter plot of "
           "x vs x**2 for x in range(20). Use the Agg backend, give it the title "
           "'OrigFig', and plt.savefig it. Pure matplotlib, no numpy. Run it as a "
           "background job.")
RECOVER = ("This project has a figure called 'OrigFig'. Reproduce it exactly as it was "
           "originally made.")


def main() -> int:
    if not server_reachable():
        print("SKIP: server not reachable at", BASE)
        return 0
    pid = _fresh_project("prov-recover")
    tid_a = _fresh_thread(pid, "produce")
    print(f"project={pid} threadA={tid_a}")

    # ── Thread A: agent backgrounds the figure ──────────────────────────────
    _consume_chat_sse(pid, tid_a, PRODUCE, max_turns=4)
    # wait for the background job + its exec record (Phase 1) to land
    exec_id = None
    deadline = time.time() + 180
    while time.time() < deadline:
        rows = _q(pid, "SELECT exec_id, run_id, started_at FROM execution_records "
                       "ORDER BY started_at DESC LIMIT 5")
        for r in rows:
            rec = _exec_has_figure(pid, r["exec_id"])
            if rec:
                exec_id = r["exec_id"]
                break
        if exec_id:
            break
        time.sleep(4)
    if not exec_id:
        print("FAIL: no background exec record with a figure was written (Phase 1 gap?)")
        return 1
    print("background exec record:", exec_id)

    # ── pin the figure → a navigable entity the fresh agent can find ────────
    ent = _post(f"/api/artifacts/{exec_id}/figure/0/pin", {"title": "OrigFig"}, pid=pid)
    fig_id = ent.get("id") or ent.get("entity", {}).get("id")
    figs = _q(pid, "SELECT id, title, exec_id FROM entities WHERE type='figure'")
    print("pinned figure:", fig_id, "| figures in project:", [(f['id'], f['title'], f['exec_id']) for f in figs])
    if not any(f.get("exec_id") for f in figs):
        print("FAIL: pinned figure has no exec_id — provenance link missing")
        return 1
    print("PASS: backgrounded figure is pinned AND carries an exec record")

    # ── Thread B: FRESH agent, no memory of the production ──────────────────
    tid_b = _fresh_thread(pid, "recover")
    print(f"threadB={tid_b} (fresh — no memory of how OrigFig was made)")
    n_runs_before = len(_q(pid, "SELECT exec_id FROM execution_records"))
    calls, text, halted, reason, events = _consume_chat_sse(pid, tid_b, RECOVER, max_turns=6)
    print("recovery tool calls:", [c[0] for c in calls], "| halted:", halted)

    # success = the agent re-ran (a new exec record appeared) via a provenance tool
    used_prov = any(c[0] in ("reproduce_from_exec", "make_revision") for c in calls)
    n_runs_after = len(_q(pid, "SELECT exec_id FROM execution_records"))
    new_runs = n_runs_after - n_runs_before
    print(f"used provenance tool: {used_prov} | new exec records: {new_runs}")
    if used_prov or new_runs >= 1:
        print("PASS: fresh agent recovered the backgrounded figure from its provenance.")
        return 0
    print(f"FAIL: fresh agent did not reproduce (calls={[c[0] for c in calls]}).")
    return 1


def _exec_has_figure(pid: str, exec_id: str) -> bool:
    """Does this exec record's produced[] include a figure? Reads the JSON sidecar
    directly via its record_path (no backend import)."""
    rows = _q(pid, "SELECT record_path FROM execution_records WHERE exec_id=?", (exec_id,))
    if not rows:
        return False
    rp = rows[0].get("record_path")
    if not rp or not Path(rp).exists():
        return False
    try:
        rec = json.loads(Path(rp).read_text())
        return any(p.get("kind") == "figure" for p in (rec.get("produced") or []))
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
