#!/usr/bin/env python3
"""record_v0.py — the "v0 Record" prototype (misc/alt_uis.md §2.6 / §5).

A READ-ONLY, fully mechanical markdown render of an EXISTING aba project, laid
out as the three strata of the "living lab notebook":

  COVER      — project name, vitals, recent activity, flags
  QUESTIONS  — per-thread narrative stubs (question + claims + pinned results)
  FIELD NOTES / TRAILS — observation-like entities if the type exists
  SEDIMENT   — chronological run (analysis) index, one line each

It never writes to any project DB or ~/.aba state: DBs are opened
`sqlite3 … mode=ro`, and the projects directory is resolved by replicating
core.config's precedence WITHOUT importing the backend (which would mkdir
runtime dirs on import). All project content is treated as OPAQUE DATA and
rendered verbatim-truncated — never interpreted or summarized.

Usage:
    record_v0.py <project_id_or_name> [output.md]
    record_v0.py --list
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

TRUNC = 200  # ~200-char field truncation, per spec

CAPABILITY_TYPE = "capability"  # seeded system tool catalog — not project content
WORKSPACE_TYPE = "workspace"

# Observation-like types that would populate the FIELD NOTES / TRAILS stratum.
OBSERVATION_TYPES = ("observation", "trail", "finding", "note")
# Claim-like / result-like types the QUESTIONS stratum looks for per thread.
CLAIM_TYPES = ("claim",)
RESULT_TYPES = ("result", "figure", "table")


# ─────────────────────────── path resolution (read-only) ───────────────────
def projects_dir() -> Path:
    """Replicate core.config.PROJECTS_DIR precedence without importing it:
        ABA_PROJECTS_DIR
          else (ABA_RUNTIME_DIR else (ABA_HOME else ~/.aba)/runtime) / projects
    """
    v = os.getenv("ABA_PROJECTS_DIR")
    if v:
        return Path(v).resolve()
    rt = os.getenv("ABA_RUNTIME_DIR")
    if rt:
        runtime = Path(rt).resolve()
    else:
        home = os.getenv("ABA_HOME") or str(Path.home() / ".aba")
        runtime = (Path(home) / "runtime").resolve()
    return (runtime / "projects").resolve()


def load_registry(pdir: Path) -> list[dict]:
    reg = pdir / "registry.json"
    try:
        return json.loads(reg.read_text())
    except Exception:
        return []


# ─────────────────────────────── db helpers ────────────────────────────────
def open_ro(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def table_exists(c: sqlite3.Connection, name: str) -> bool:
    try:
        return c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except Exception:
        return False


def columns(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def q(c: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Defensive query: any failure (missing table/column) → empty list."""
    try:
        return list(c.execute(sql, params).fetchall())
    except Exception:
        return []


def jload(s) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


# ─────────────────────────────── formatting ────────────────────────────────
def trunc(s, n: int = TRUNC) -> str:
    if s is None:
        return ""
    s = str(s).replace("\r", " ").replace("\n", " ").strip()
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def fmt_ts(s) -> str:
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(s)[:16]


def md_escape(s: str) -> str:
    # Minimal: keep opaque titles from breaking table/pipe rows.
    return str(s).replace("|", "\\|")


# ─────────────────────────────── entity access ─────────────────────────────
def all_entities(c: sqlite3.Connection) -> list[dict]:
    cols = columns(c, "entities")
    if not cols:
        return []
    # Only select columns that exist (schema may vary across DB vintages).
    want = [x for x in (
        "id", "type", "title", "status", "pinned", "parent_entity_id",
        "metadata", "notes", "derivation", "created_at", "updated_at",
        "deleted_at",
    ) if x in cols]
    rows = q(c, f"SELECT {', '.join(want)} FROM entities")
    out = []
    for r in rows:
        d = dict(r)
        if d.get("deleted_at"):
            continue
        if (d.get("status") or "") == "archived":
            continue
        d["_md"] = jload(d.get("metadata"))
        out.append(d)
    return out


def run_outputs(md: dict) -> list:
    run = md.get("run")
    if isinstance(run, dict) and isinstance(run.get("outputs"), list):
        return run["outputs"]
    return []


# ─────────────────────────────── strata renders ────────────────────────────
def render_cover(out, name, pid, ents, c):
    by_type: dict[str, int] = {}
    for e in ents:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    def n(t):
        return by_type.get(t, 0)

    threads = [e for e in ents if e["type"] == "thread"]
    analyses = [e for e in ents if e["type"] == "analysis"]
    datasets = [e for e in ents if e["type"] == "dataset"]
    claims = [e for e in ents if e["type"] in CLAIM_TYPES]
    results = [e for e in ents if e["type"] in RESULT_TYPES]

    out.append(f"# {md_escape(name)}")
    out.append("")
    out.append(f"*Record v0 — a read-only render of project `{pid}`. "
               f"Generated {fmt_ts(datetime.now(timezone.utc).isoformat())} UTC.*")
    out.append("")
    out.append("## Cover")
    out.append("")

    # Vitals ----------------------------------------------------------------
    out.append("### Vitals")
    out.append("")
    out.append(f"- Datasets: **{len(datasets)}**")
    out.append(f"- Runs (analyses): **{len(analyses)}**")
    out.append(f"- Threads: **{len(threads)}**")
    out.append(f"- Plans: **{n('plan')}**")

    # Claims by status / confidence
    if claims:
        by_status: dict[str, int] = {}
        by_conf: dict[str, int] = {}
        for e in claims:
            by_status[e["_md"].get("status") or e.get("status") or "?"] = \
                by_status.get(e["_md"].get("status") or e.get("status") or "?", 0) + 1
            by_conf[str(e["_md"].get("confidence") or "?")] = \
                by_conf.get(str(e["_md"].get("confidence") or "?"), 0) + 1
        out.append(f"- Claims: **{len(claims)}** "
                   f"(status: {', '.join(f'{k}×{v}' for k, v in sorted(by_status.items()))}; "
                   f"confidence: {', '.join(f'{k}×{v}' for k, v in sorted(by_conf.items()))})")
    else:
        out.append("- Claims: **0** — *(no claim entities in this project)*")

    if results:
        out.append(f"- Results: **{len(results)}** "
                   f"({sum(1 for e in results if e.get('pinned'))} pinned)")
    else:
        out.append("- Results: **0** — *(no result/figure/table entities in this project)*")

    # Other / system types, shown transparently but not conflated with content.
    other = {t: v for t, v in sorted(by_type.items())
             if t not in ("dataset", "analysis", "thread", "plan",
                          WORKSPACE_TYPE) and t not in CLAIM_TYPES and t not in RESULT_TYPES}
    if other:
        cap = other.pop(CAPABILITY_TYPE, None)
        if cap is not None:
            out.append(f"- Capability catalog (system, not project content): {cap}")
        if other:
            out.append("- Other entity types: "
                       + ", ".join(f"{t}×{v}" for t, v in other.items()))
    out.append("")

    # Recent activity -------------------------------------------------------
    out.append("### Recent activity")
    out.append("")
    activity: list[tuple[str, str]] = []
    if table_exists(c, "events"):
        for r in q(c, "SELECT kind, entity_id, title, ts FROM events "
                      "ORDER BY ts DESC LIMIT 30"):
            label = r["title"] or r["entity_id"] or ""
            activity.append((r["ts"] or "", f"{r['kind']}: {trunc(label, 80)}"))
    # Entity updates (updated_at strictly after created_at) as a second source.
    for e in ents:
        if e["type"] in (CAPABILITY_TYPE, WORKSPACE_TYPE):
            continue
        ca, ua = e.get("created_at"), e.get("updated_at")
        if ua and ca and str(ua) > str(ca):
            activity.append((ua, f"updated {e['type']}: {trunc(e.get('title'), 80)}"))
    # Merge, newest first, dedupe on (minute, label).
    activity.sort(key=lambda t: t[0], reverse=True)
    seen = set()
    shown = 0
    if not activity:
        out.append("- *(no events or entity updates recorded)*")
    for ts, label in activity:
        key = (fmt_ts(ts), label)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"- `{fmt_ts(ts)}` — {md_escape(label)}")
        shown += 1
        if shown >= 10:
            break
    out.append("")

    # Flags -----------------------------------------------------------------
    out.append("### Flags")
    out.append("")
    flags = []
    for e in datasets:
        md = e["_md"]
        fp = md.get("fingerprint") if isinstance(md.get("fingerprint"), dict) else {}
        reasons = []
        if md.get("source_changed"):
            reasons.append("source_changed")
        if md.get("source_missing"):
            reasons.append("source_missing")
        if fp.get("exists") is False:
            reasons.append("fingerprint: source not found")
        if reasons:
            flags.append(f"- Drifted dataset `{e['id']}` "
                         f"\"{trunc(e.get('title'), 80)}\" — {', '.join(reasons)}")
    for e in analyses:
        alert = e["_md"].get("retention_alert")
        if alert:
            flags.append(f"- Retention alert on run `{e['id']}` "
                         f"\"{trunc(e.get('title'), 60)}\" — {trunc(alert)}")
    if flags:
        out.extend(flags)
    else:
        out.append("- *(no drift or retention flags)*")
    out.append("")


def render_questions(out, ents):
    out.append("## Questions")
    out.append("")
    threads = [e for e in ents if e["type"] == "thread"]
    analyses = [e for e in ents if e["type"] == "analysis"]
    claims = [e for e in ents if e["type"] in CLAIM_TYPES]
    results = [e for e in ents if e["type"] in RESULT_TYPES]

    if not threads:
        out.append("*(no threads in this project)*")
        out.append("")
        return

    def thread_of(e):
        return e["_md"].get("thread_id")

    with_q = [t for t in threads if (t["_md"].get("question") or "").strip()]
    without_q = [t for t in threads if not (t["_md"].get("question") or "").strip()]

    def render_thread(t):
        tid = t["id"]
        out.append(f"### {md_escape(trunc(t.get('title'), 120))}")
        md = t["_md"]
        lifecycle = md.get("lifecycle") or "—"
        out.append(f"*Thread `{tid}` · lifecycle: {lifecycle}*")
        out.append("")
        if md.get("question"):
            out.append(f"- **Question:** {trunc(md.get('question'))}")
        oq = md.get("open_questions")
        if isinstance(oq, list) and oq:
            out.append("- **Open questions:**")
            for x in oq[:10]:
                out.append(f"  - {trunc(x)}")
        out.append("")

        # Claims linked to this thread.
        my_claims = [e for e in claims if thread_of(e) == tid]
        out.append("**Claims**")
        if my_claims:
            for e in my_claims:
                m = e["_md"]
                conf = m.get("confidence")
                out.append(f"- {trunc(m.get('statement') or e.get('title'))}"
                           + (f" _(confidence: {conf})_" if conf else ""))
                if m.get("caveats"):
                    out.append(f"  - caveats: {trunc(m.get('caveats'))}")
        else:
            out.append("- *(no claims recorded on this thread)*")
        out.append("")

        # Pinned results linked to this thread.
        my_results = [e for e in results
                      if thread_of(e) == tid and e.get("pinned")]
        out.append("**Pinned results**")
        if my_results:
            for e in my_results:
                m = e["_md"]
                interp = m.get("interpretation") or e.get("notes")
                out.append(f"- {trunc(e.get('title'))}"
                           + (f" — {trunc(interp)}" if interp else ""))
        else:
            out.append("- *(no pinned results on this thread)*")
        out.append("")

        # Linked-output counts.
        my_runs = [e for e in analyses if thread_of(e) == tid]
        n_outputs = sum(len(run_outputs(e["_md"])) for e in my_runs)
        out.append(f"*Linked: {len(my_runs)} run(s), {n_outputs} output(s).*")
        out.append("")

    for t in with_q:
        render_thread(t)

    if without_q:
        out.append("## Exploration")
        out.append("")
        out.append("*Threads without a stated question.*")
        out.append("")
        for t in without_q:
            render_thread(t)


def render_field_notes(out, ents, c):
    out.append("## Field notes / Trails")
    out.append("")
    obs = [e for e in ents if e["type"] in OBSERVATION_TYPES]
    n_agent_notes = 0
    if table_exists(c, "agent_notes"):
        rows = q(c, "SELECT COUNT(*) n FROM agent_notes WHERE status != 'deleted'")
        if rows:
            n_agent_notes = rows[0]["n"]

    if not obs and not n_agent_notes:
        out.append("*This stratum is empty — no observation/trail entities "
                   "(types: " + ", ".join(OBSERVATION_TYPES) + ") and no agent "
                   "notes in this project.*")
        out.append("")
        return

    if obs:
        for e in obs:
            m = e["_md"]
            out.append(f"- **{md_escape(trunc(e.get('title'), 100))}** "
                       f"(`{e['type']}` {e['id']})")
            body = m.get("body") or m.get("text") or e.get("notes")
            if body:
                out.append(f"  - {trunc(body)}")
    if n_agent_notes:
        out.append(f"- *(plus {n_agent_notes} agent note(s) in the "
                   f"`agent_notes` table)*")
    out.append("")


def render_sediment(out, ents):
    out.append("## Sediment")
    out.append("")
    out.append("*Chronological run index — one line per analysis run.*")
    out.append("")
    analyses = [e for e in ents if e["type"] == "analysis"]
    if not analyses:
        out.append("*(no analysis runs in this project)*")
        out.append("")
        return
    analyses.sort(key=lambda e: str(e.get("created_at") or ""))
    for e in analyses:
        md = e["_md"]
        outs = run_outputs(md)
        state = md.get("run_state") or e.get("status") or "?"
        markers = []
        run = md.get("run") if isinstance(md.get("run"), dict) else {}
        fs = run.get("failed_steps")
        if fs:
            markers.append(f"{fs} failed step(s)")
        if md.get("retention_alert"):
            markers.append("retention-alert")
        if md.get("keep_decision"):
            markers.append("kept✓")
        if md.get("ambient"):
            markers.append("ambient")
        mk = f" · {'; '.join(markers)}" if markers else ""
        out.append(f"- `{fmt_ts(e.get('created_at'))}` · "
                   f"{md_escape(trunc(e.get('title'), 90))} · "
                   f"{state} · {len(outs)} output(s){mk}")
    out.append("")


# ─────────────────────────────── project resolve ───────────────────────────
def resolve_project(arg: str, pdir: Path, registry: list[dict]):
    """Return (pid, name) for a project id or (case-insensitive) name, else None."""
    by_id = {p.get("id"): p for p in registry}
    # Exact id match.
    if arg in by_id:
        return arg, by_id[arg].get("name") or arg
    # A bare directory that exists on disk, even if not in the registry.
    if (pdir / arg / "project.db").exists():
        return arg, by_id.get(arg, {}).get("name") or arg
    # Name match (exact, then substring), case-insensitive.
    la = arg.lower()
    exact = [p for p in registry if (p.get("name") or "").lower() == la]
    if len(exact) == 1:
        return exact[0]["id"], exact[0]["name"]
    sub = [p for p in registry if la in (p.get("name") or "").lower()]
    if len(sub) == 1:
        return sub[0]["id"], sub[0].get("name") or sub[0]["id"]
    return None


def rank_projects(pdir: Path, registry: list[dict]) -> list[tuple]:
    names = {p.get("id"): p.get("name") for p in registry}
    ranked = []
    for d in sorted(pdir.glob("prj_*")):
        db = d / "project.db"
        c = open_ro(db)
        if c is None:
            continue
        rows = q(c, "SELECT type, COUNT(*) n FROM entities "
                    "WHERE deleted_at IS NULL AND status!='archived' GROUP BY type")
        c.close()
        tc = {r["type"]: r["n"] for r in rows}
        sub = sum(v for t, v in tc.items()
                  if t not in (CAPABILITY_TYPE, WORKSPACE_TYPE))
        ranked.append((sub, d.name, names.get(d.name, "?"), tc))
    ranked.sort(reverse=True)
    return ranked


# ─────────────────────────────────── main ──────────────────────────────────
def build_record(pid: str, name: str, db_path: Path) -> str:
    out: list[str] = []
    c = open_ro(db_path)
    if c is None:
        return f"# {name}\n\n*Could not open project DB `{db_path}` read-only.*\n"
    ents = all_entities(c)
    render_cover(out, name, pid, ents, c)
    render_questions(out, ents)
    render_field_notes(out, ents, c)
    render_sediment(out, ents)
    c.close()
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    pdir = projects_dir()
    registry = load_registry(pdir)

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if argv[0] == "--list":
        ranked = rank_projects(pdir, registry)
        print(f"Projects dir: {pdir}")
        print(f"{'substantive':>11}  {'pid':<14} name  [types]")
        for sub, pid, name, tc in ranked[:25]:
            sc = {t: v for t, v in tc.items()
                  if t not in (CAPABILITY_TYPE, WORKSPACE_TYPE)}
            print(f"{sub:>11}  {pid:<14} {name!r}  {sc}")
        return 0

    resolved = resolve_project(argv[0], pdir, registry)
    if resolved is None:
        print(f"error: could not resolve project '{argv[0]}' under {pdir}",
              file=sys.stderr)
        print("Try `record_v0.py --list` to see available projects.",
              file=sys.stderr)
        return 1
    pid, name = resolved
    db_path = pdir / pid / "project.db"
    record = build_record(pid, name, db_path)

    if len(argv) >= 2:
        Path(argv[1]).write_text(record)
        print(f"wrote {len(record)} bytes to {argv[1]}", file=sys.stderr)
    else:
        sys.stdout.write(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
