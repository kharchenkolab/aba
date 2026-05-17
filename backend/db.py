import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "aba.db"

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                role    TEXT    NOT NULL,
                content TEXT    NOT NULL,
                ts      TEXT    NOT NULL
            )
        """)
        c.commit()

def append_message(role: str, content_blocks: list) -> int:
    ts = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
            (role, json.dumps(content_blocks), ts)
        )
        c.commit()
        return cur.lastrowid

def get_all_messages():
    with _conn() as c:
        rows = c.execute("SELECT role, content, ts FROM messages ORDER BY id").fetchall()
    return [{"role": r["role"], "content": json.loads(r["content"]), "ts": r["ts"]} for r in rows]

def clear_history():
    with _conn() as c:
        c.execute("DELETE FROM messages")
        c.commit()
