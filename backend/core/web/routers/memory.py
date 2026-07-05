"""Agent-memory read routes — index + per-entry fetch for the current project's
memory directory. Extracted from main.py (Item 2A.3). Domain-neutral (core.memory)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/memory")
def memory_list():
    """Index + entry list for the current project's memory directory.
    Entries omit bodies; fetch one via /api/memory/{name}."""
    from core.memory import list_memories, read_memory_index
    return {
        "index": read_memory_index(),
        "entries": [
            {"name": e.name, "type": e.type, "description": e.description}
            for e in list_memories()
        ],
    }


@router.get("/api/memory/{name}")
def memory_get(name: str):
    """Full memory body + metadata."""
    from core.memory import read_memory
    e = read_memory(name)
    if e is None:
        raise HTTPException(404, f"memory {name!r} not found")
    return {
        "name": e.name,
        "type": e.type,
        "description": e.description,
        "body": e.body,
    }
