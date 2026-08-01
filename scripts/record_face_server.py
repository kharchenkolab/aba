#!/usr/bin/env python3
"""Record face sidecar — coexistence option A (RECORD_DESIGN §13.3 phase 1).

Serves the Record's read-only World over an EXISTING deployment's project
DBs without running the deployment: only the record route's logic is
mounted — main.py, the lifespan, recovery, stale-turn reaping, and content-
pack hooks never load. The record path executes only SELECTs, and projects
are bound per-request via the low-level contextvar (`bind_active_db`), not
via `projects.set_current`, so no on-project-open hook can fire.

Run from the branch checkout, beside a live server, on its own port:

    python3 scripts/record_face_server.py                 # ~/.aba/runtime, :8020
    python3 scripts/record_face_server.py --runtime DIR --port N

Then open  notebook.html?live=1&api=http://127.0.0.1:8020&project=<pid>
(project ids:  GET /api/record/projects).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def build_app(runtime: Path):
    """The sidecar app over one runtime dir. Importable — the growth demo
    serves its staged runtime through exactly this app."""
    runtime = Path(runtime).expanduser().resolve()
    if not (runtime / "projects").is_dir():
        raise SystemExit(f"no projects/ under {runtime} — is --runtime right?")
    os.environ["ABA_RUNTIME_DIR"] = str(runtime)

    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware

    from core.graph._schema import _active_db_path, bind_active_db
    from core.record.world import assemble_world, register_record_roles

    # Same mapping as content/bio/record_roles.py — duplicated here on
    # purpose: importing the pack would wire its on-project-open hooks,
    # which this sidecar exists to avoid. Keep in sync.
    register_record_roles(
        {"question": "thread", "claim": "claim",
         "prose": "narrative", "note": "note"},
        maturity_order=("preliminary", "supported", "validated",
                        "contested", "refuted"),
        artifact_types=("figure", "table", "cell"),
        maturity_key="confidence",
        prose_body_key="text",
        claim_statement_key="statement",
    )

    app = FastAPI(title="record-face-sidecar")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                       "http://localhost:5174", "http://127.0.0.1:5174"],
        allow_methods=["GET"], allow_headers=["*"])

    def _db_of(pid: str) -> Path:
        # resolve without core.config.project_root (it mkdirs); read-only
        p = runtime / "projects" / pid / "project.db"
        if not p.is_file():
            raise HTTPException(404, f"no project.db for {pid!r}")
        return p

    @app.get("/api/record/projects")
    def projects_list():
        out = [{"id": db.parent.name}
               for db in sorted((runtime / "projects").glob("*/project.db"))]
        return {"projects": out, "runtime": str(runtime)}

    @app.get("/api/record/world")
    def record_world(project_id: str, sediment_limit: int = 200,
                     since: str | None = None):
        token = bind_active_db(_db_of(project_id))
        try:
            world = assemble_world(sediment_limit=sediment_limit, since=since)
        finally:
            _active_db_path.reset(token)
        world["project_id"] = project_id
        return world

    return app


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime",
                        default=os.path.expanduser("~/.aba/runtime"))
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app = build_app(Path(args.runtime))
    print(f"record face sidecar: {args.runtime} on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
