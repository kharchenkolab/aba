import asyncio
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
    add_edge,
    edges_from,
    edges_to,
    WORKSPACE_ID,
)
from guide import stream_response
from promote import (
    promote_figure_to_result,
    promote_results_to_finding,
    promote_findings_to_claim,
)
from scenarios import create_scenario_variant


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


# ---------- Promotion / result chain ----------

class PromoteFigureRequest(BaseModel):
    interpretation: str
    title: str | None = None


class PromoteResultsRequest(BaseModel):
    result_ids: list[str]
    text: str
    title: str | None = None


class PromoteFindingsRequest(BaseModel):
    finding_ids: list[str]
    text: str
    title: str | None = None


@app.post("/api/entities/{figure_id}/promote-to-result")
def promote_to_result(figure_id: str, req: PromoteFigureRequest):
    try:
        rid = promote_figure_to_result(figure_id, req.interpretation, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(rid)


@app.post("/api/findings")
def create_finding(req: PromoteResultsRequest):
    try:
        fid = promote_results_to_finding(req.result_ids, req.text, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(fid)


@app.post("/api/claims")
def create_claim(req: PromoteFindingsRequest):
    try:
        cid = promote_findings_to_claim(req.finding_ids, req.text, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(cid)


@app.get("/api/entities/{entity_id}/edges")
def entities_edges(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return {
        "outgoing": edges_from(entity_id),
        "incoming": edges_to(entity_id),
    }


# ---------- Scenarios ----------

class ScenarioRequest(BaseModel):
    description: str
    # Optional: skip the LLM rewrite by supplying the new code directly.
    code: str | None = None
    title: str | None = None


@app.post("/api/entities/{baseline_id}/create-scenario")
async def create_scenario(baseline_id: str, req: ScenarioRequest):
    try:
        new_entity = await asyncio.get_event_loop().run_in_executor(
            None,
            create_scenario_variant,
            baseline_id, req.description, req.code, req.title,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return new_entity


# ---------- Upload ----------

def _unique_path(dest: Path) -> Path:
    """Suffix the filename if it already exists in the dir."""
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    i = 1
    while True:
        candidate = dest.parent / f"{stem}_{i}{suf}"
        if not candidate.exists():
            return candidate
        i += 1


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Drop an uploaded file into DATA_DIR, register as a 'dataset' entity."""
    if not file.filename:
        raise HTTPException(400, "filename missing")
    safe_name = Path(file.filename).name
    dest = _unique_path(DATA_DIR / safe_name)
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


class URLUploadRequest(BaseModel):
    url: str
    title: str | None = None


@app.post("/api/upload-url")
async def upload_url(req: URLUploadRequest):
    """
    Download a file from a URL into DATA_DIR and register a dataset entity.

    The Guide can later inspect/unpack the file (e.g. tar.gz of a 10x folder)
    via tool calls. This endpoint just lands the bytes locally.
    """
    import urllib.parse
    import urllib.request
    import urllib.error
    parsed = urllib.parse.urlparse(req.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "only http(s) URLs are supported")
    name = Path(parsed.path).name or "downloaded.bin"
    dest = _unique_path(DATA_DIR / name)

    try:
        with urllib.request.urlopen(req.url, timeout=60) as resp:
            total = 0
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    # Cap absurd payloads to avoid filling the disk.
                    if total > 2 * 1024 * 1024 * 1024:
                        raise HTTPException(413, "remote file > 2GB; aborting")
    except urllib.error.URLError as e:
        raise HTTPException(400, f"download failed: {e}")

    eid = create_entity(
        entity_type="dataset",
        title=req.title or dest.name,
        artifact_path=str(dest),
        metadata={
            "size_bytes": dest.stat().st_size,
            "source_url": req.url,
            "original_name": name,
        },
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
