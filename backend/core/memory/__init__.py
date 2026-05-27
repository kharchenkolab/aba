"""Typed memory subsystem — per-project markdown memories with an
always-loaded index.

B3: mirrors Claude Code's auto-memory pattern. Project-local
`memory/` directory holds:
  - MEMORY.md           — one-line index, always loaded into the prompt
  - <name>.md           — typed memory files (frontmatter: name, description, type)

Types: user, feedback, project, reference (the same four Claude Code
uses, because the distinction is task-shaping, not domain-specific).

Tools: read_memory(name), write_memory(name, body, type, description).
"""
from .typed_files import (
    MemoryEntry,
    MEMORY_TYPES,
    memory_dir,
    list_memories,
    read_memory_index,
    read_memory,
    write_memory,
    delete_memory,
    memory_index_block,
)

__all__ = [
    "MemoryEntry",
    "MEMORY_TYPES",
    "memory_dir",
    "list_memories",
    "read_memory_index",
    "read_memory",
    "write_memory",
    "delete_memory",
    "memory_index_block",
]
