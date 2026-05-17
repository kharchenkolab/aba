import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path

from config import ARTIFACTS_DIR
from db import init_db, get_all_messages, clear_history
from guide import stream_response

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated plot PNGs
app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")

@app.on_event("startup")
def startup():
    init_db()


class ChatRequest(BaseModel):
    text: str


@app.get("/api/history")
def history():
    """Return all stored messages for frontend to render on load."""
    return get_all_messages()


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Stream a Guide response to the user's message."""
    async def event_stream():
        async for chunk in stream_response(req.text):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.delete("/api/history")
def delete_history():
    clear_history()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}
