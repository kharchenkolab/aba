import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import ARTIFACTS_DIR, DATA_DIR
from db import (
    init_db,
    get_messages,
    clear_messages,
    list_entities,
    get_entity,
    create_entity,
    WORKSPACE_ID,
)
from guide import stream_response


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")


@app.on_event("startup")
def startup():
    init_db()


# ---------- Entities ----------

@app.get("/api/entities")
def entities_list():
    """Project tree feed. Workspace root is included as the first row."""
    return list_entities()


@app.get("/api/entities/{entity_id}")
def entities_get(entity_id: str):
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return e


@app.get("/api/entities/{entity_id}/messages")
def entities_messages(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return get_messages(entity_id)


@app.delete("/api/entities/{entity_id}/messages")
def entities_clear_messages(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    clear_messages(entity_id)
    return {"ok": True}


@app.get("/api/entities/{entity_id}/preview")
def entities_preview(entity_id: str, limit: int = 20):
    """
    Return a lightweight preview for an entity's artifact.

    Currently supported types:
      - dataset (CSV/TSV): first N rows + column names + total row count.
    Returns {"kind": "table", "columns": [...], "rows": [[...], ...], "total_rows": N}
    or {"kind": "none"} if no preview is available.
    """
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")

    if e["type"] == "dataset" and e["artifact_path"]:
        path = Path(e["artifact_path"])
        if path.suffix.lower() in (".csv", ".tsv") and path.exists():
            try:
                import pandas as pd
                sep = "," if path.suffix.lower() == ".csv" else "\t"
                df = pd.read_csv(path, sep=sep, nrows=limit)
                # Get total row count without re-reading the whole frame.
                with path.open("r") as f:
                    total = sum(1 for _ in f) - 1
                return {
                    "kind": "table",
                    "columns": [str(c) for c in df.columns],
                    "rows": df.astype(object).where(df.notna(), None).values.tolist(),
                    "total_rows": max(total, 0),
                    "shown": min(limit, len(df)),
                }
            except Exception as ex:  # noqa: BLE001
                return {"kind": "error", "error": str(ex)}

    return {"kind": "none"}


# ---------- Chat ----------

class ChatRequest(BaseModel):
    text: str
    # The entity the user is *focused on* (chip / canvas). Used to augment
    # the model's context. The chat thread itself is always project-level.
    focus_entity_id: str = WORKSPACE_ID


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not get_entity(req.focus_entity_id):
        raise HTTPException(404, f"Entity {req.focus_entity_id} not found")

    async def event_stream():
        async for chunk in stream_response(req.text, focus_entity_id=req.focus_entity_id):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/messages")
def messages_list():
    """The project's running conversation (workspace thread)."""
    return get_messages(WORKSPACE_ID)


# ---------- Upload ----------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """
    Accept a file upload, drop it into DATA_DIR, register as a 'dataset' entity.
    Returns the new entity record.
    """
    if not file.filename:
        raise HTTPException(400, "filename missing")

    # Sanitize: take basename only, no path traversal.
    safe_name = Path(file.filename).name
    dest = DATA_DIR / safe_name
    # If a file with that name already exists, suffix to avoid clobbering.
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        i = 1
        while True:
            candidate = DATA_DIR / f"{stem}_{i}{suf}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1

    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    size = dest.stat().st_size
    eid = create_entity(
        entity_type="dataset",
        title=dest.name,
        artifact_path=str(dest),
        metadata={"size_bytes": size, "original_name": file.filename},
    )
    return get_entity(eid)


# ---------- Legacy aliases (kept until frontend migration is done) ----------

@app.get("/api/history")
def history_legacy():
    return get_messages(WORKSPACE_ID)


@app.delete("/api/history")
def history_clear_legacy():
    clear_messages(WORKSPACE_ID)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}
