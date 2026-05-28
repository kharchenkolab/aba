"""
Token-free end-to-end smoke test.

Boots the FastAPI app in-process with ABA_FAKE_SESSION pointing at a scripted
fixture, posts a user message to /api/chat, and asserts that:
  - The scripted assistant text streamed back as SSE deltas
  - The real tool (list_data_files) actually ran and its result was streamed
  - The session terminated cleanly

No Anthropic API call is made; no ANTHROPIC_API_KEY required.

Run:
    .venv/bin/python tests/smoke_fake.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Isolate state from a developer's real DB / artifacts before importing the app.
# Isolation is via env vars read at import time by core.config /
# core.graph._schema — set them BEFORE importing the app.
tmpdir = tempfile.mkdtemp(prefix="aba_smoke_")
os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/list_files.jsonl")
os.environ["ABA_DB_PATH"] = str(Path(tmpdir) / "smoke.db")  # never touch aba.db
os.environ["ARTIFACTS_DIR"] = str(Path(tmpdir) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(tmpdir) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(tmpdir) / "envs")
os.environ.setdefault("DATA_DIR", str(ROOT / "backend/data"))

sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402


def parse_sse(body: str):
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def main() -> int:
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"text": "what's here?"}) as resp:
            assert resp.status_code == 200, resp.status_code
            body = "".join(chunk for chunk in resp.iter_text())

    events = parse_sse(body)
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    tool_results = [e for e in events if e["type"] == "tool_result"]
    done = [e for e in events if e["type"] == "done"]

    print(f"events: {len(events)}  deltas: {len(deltas)} chars  "
          f"tool_results: {len(tool_results)}  done: {len(done)}")

    assert "data folder" in deltas, f"missing first turn text in: {deltas!r}"
    assert "Found 2 CSV files" in deltas, f"missing second turn text in: {deltas!r}"
    assert len(tool_results) == 1, f"expected 1 tool_result, got {len(tool_results)}"
    assert tool_results[0]["name"] == "list_data_files"
    files = tool_results[0]["result"].get("files", [])
    assert any(f["filename"] == "cells.csv" for f in files), files
    assert done, "no done event"

    print("OK — fake-mode smoke test passed (zero tokens spent).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
