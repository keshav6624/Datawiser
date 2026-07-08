"""Persistence layer using SQLite for app metadata.

Caches: data sources, schema snapshots, conversations, query history, dashboards.
The actual data being analyzed lives in the user's own sources (CSV / DB).
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .config import DB_PATH

_DDL = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_cache (
    source_id TEXT PRIMARY KEY,
    schema_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    question TEXT,
    answer_json TEXT,
    sql TEXT,
    execution_time REAL,
    status TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dashboards (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def uid() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


# ---------- source helpers ----------

def save_source(conn, source_id: str, org_id: str, name: str, type_: str, config: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sources (id, org_id, name, type, config_json, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (source_id, org_id, name, type_, json.dumps(config), "active", now()),
    )
    conn.commit()


def list_org_sources(conn, org_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM sources WHERE org_id=? ORDER BY created_at DESC", (org_id,)).fetchall()
    return [dict(r) for r in rows]


def get_source(conn, source_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    return dict(r) if r else None


def delete_source(conn, source_id: str) -> None:
    conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    conn.execute("DELETE FROM schema_cache WHERE source_id=?", (source_id,))
    conn.commit()


def cache_schema(conn, source_id: str, schema: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO schema_cache (source_id, schema_json, updated_at) VALUES (?,?,?)",
        (source_id, json.dumps(schema, default=str), now()),
    )
    conn.commit()


def get_cache_schema(conn, source_id: str) -> Any | None:
    r = conn.execute("SELECT schema_json FROM schema_cache WHERE source_id=?", (source_id,)).fetchone()
    if not r:
        return None
    return json.loads(r["schema_json"])


# ---------- conversation helpers ----------

def create_conversation(conn, org_id: str, user_id: str, source_id: str, title: str) -> str:
    cid = uid()
    conn.execute(
        "INSERT INTO conversations (id, org_id, user_id, source_id, title, created_at) VALUES (?,?,?,?,?,?)",
        (cid, org_id, user_id, source_id, title, now()),
    )
    conn.commit()
    return cid


def list_conversations(conn, org_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM conversations WHERE org_id=? ORDER BY created_at DESC", (org_id,)).fetchall()
    return [dict(r) for r in rows]


def add_message(conn, conversation_id: str, question: str, answer_json: str, sql: str, exec_time: float, status: str) -> str:
    mid = uid()
    conn.execute(
        "INSERT INTO messages (id, conversation_id, question, answer_json, sql, execution_time, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (mid, conversation_id, question, answer_json, sql, exec_time, status, now()),
    )
    conn.commit()
    return mid


def list_messages(conn, conversation_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC", (conversation_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["answer"] = json.loads(d.pop("answer_json") or "null")
        except Exception:
            d["answer"] = None
        out.append(d)
    return out


def get_history(conn, org_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.* FROM messages m JOIN conversations c ON m.conversation_id=c.id WHERE c.org_id=? ORDER BY m.created_at DESC LIMIT 100",
        (org_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["answer"] = json.loads(d.pop("answer_json") or "null")
        except Exception:
            d["answer"] = None
        out.append(d)
    return out


# ---------- dashboard helpers ----------

def save_dashboard(conn, org_id: str, owner: str, title: str, layout: dict, dashboard_id: str | None = None) -> str:
    did = dashboard_id or uid()
    exists = conn.execute("SELECT id FROM dashboards WHERE id=?", (did,)).fetchone()
    if exists:
        conn.execute("UPDATE dashboards SET title=?, layout_json=?, updated_at=? WHERE id=?",
                      (title, json.dumps(layout), now(), did))
    else:
        conn.execute(
            "INSERT INTO dashboards (id, org_id, owner, title, layout_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (did, org_id, owner, title, json.dumps(layout), now(), now()),
        )
    conn.commit()
    return did


def list_dashboards(conn, org_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM dashboards WHERE org_id=? ORDER BY updated_at DESC", (org_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["layout"] = json.loads(d.pop("layout_json") or "{}")
        except Exception:
            d["layout"] = {}
        out.append(d)
    return out


def get_dashboard(conn, dashboard_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["layout"] = json.loads(d.pop("layout_json") or "{}")
    except Exception:
        d["layout"] = {}
    return d
