"""Local approval metadata. Never persist prompts, commands, or credentials."""
from contextlib import closing
from pathlib import Path
import sqlite3
from uuid import uuid4

from keybow_net.pairing import CONFIG_DIR


class ApprovalAudit:
    def __init__(self, path=CONFIG_DIR / "approvals.sqlite3"):
        self.path = Path(path)
        self.instance = uuid4().hex

    def record(self, request, event, source, decision=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS approval_events (
                    id INTEGER PRIMARY KEY,
                    utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    instance TEXT NOT NULL, generation INTEGER NOT NULL,
                    thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    item_id TEXT NOT NULL, rpc_id NOT NULL, kind TEXT NOT NULL,
                    event TEXT NOT NULL, source TEXT NOT NULL, decision TEXT
                )
            """)
            db.execute("""
                INSERT INTO approval_events (
                    instance, generation, thread_id, turn_id, item_id, rpc_id,
                    kind, event, source, decision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.instance, request.generation, request.thread_id,
                  request.turn_id, request.item_id, request.rpc_id,
                  request.kind, event, source, decision))
