"""FastAPI application: DataWhisper.

Exposes the full API surface described in the PRD:
  POST /api/login
  POST /api/upload
  POST /api/connect-db
  GET  /api/sources
  DELETE /api/sources/{id}
  GET  /api/schema
  POST /api/chat
  POST /api/generate-sql
  POST /api/execute
  POST /api/save-dashboard
  GET  /api/dashboards
  GET  /api/history
  GET  /api/insights/{conversation_id}
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import persistence
from .config import settings, UPLOAD_DIR
from .datasource import TableSchema, build_connector, SQLiteConnector
from .schema_understanding import schema_to_json
from .sqlengine import run_pipeline, detect_intent, generate_sql, validate_sql, build_plan
from .insights import generate_insight, select_chart, generate_followups
import collections


import threading

# We cache connectors per source so we don't reopen DBs per request.
_connectors: dict[str, Any] = {}
_locks: dict[str, threading.Lock] = collections.defaultdict(threading.Lock)


def _org_id(request: Request) -> str:
    # Single-tenant demo workspace; in production this would come from auth.
    return request.headers.get("x-org-id") or "org_default"


def _build_connector(source_id: str) -> Any:
    if source_id in _connectors:
        return _connectors[source_id]
    conn = persistence.get_db()
    src = persistence.get_source(conn, source_id)
    if not src:
        conn.close()
        raise HTTPException(404, "Source not found")
    cfg = json.loads(src["config_json"])
    cfg.pop("source_id", None)
    c = build_connector(src["type"], source_id=source_id, **cfg)
    _connectors[source_id] = c
    conn.close()
    return c


app = FastAPI(title=settings.app_name, version=settings.version, docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _coerce_rows(rows: list) -> list:
    """JSON-friendly: datetime/date/Decimal -> strings."""
    from datetime import date, datetime
    from decimal import Decimal
    out = []
    for r in rows:
        new = {}
        for k, v in r.items():
            if isinstance(v, (datetime, date)):
                new[k] = v.isoformat()
            elif isinstance(v, Decimal):
                new[k] = float(v)
            else:
                new[k] = v
        out.append(new)
    return out


@app.get("/api")
def health() -> dict:
    return {"app": settings.app_name, "version": settings.version, "llm": settings.llm_provider, "status": "ok"}


@app.post("/api/login")
def login(email: str = Form(...), name: str = Form("")) -> dict:
    org_id = "org_default"
    user_id = "user_" + email.split("@")[0]
    return {
        "token": f"demo-{user_id}",
        "user": {"id": user_id, "name": name or email, "email": email},
        "org_id": org_id,
        "message": "Mock auth: any user is accepted in this demo. RBAC is wired in the schema.",
    }


@app.post("/api/upload")
async def upload(request: Request, name: str = Form(...), files: list[UploadFile] = File(...)) -> dict:
    org = _org_id(request)
    source_id = f"src_{uuid.uuid4().hex[:8]}"
    save_dir = UPLOAD_DIR / source_id
    save_dir.mkdir(exist_ok=True)
    saved: list[str] = []
    for f in files:
        p = save_dir / f.filename
        with open(p, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(str(p))
    cfg = {"files": saved}
    conn = persistence.get_db()
    # build schema right away
    try:
        c = build_connector("csv", source_id=source_id, files=[Path(x) for x in saved])
        schema = c.get_schema()
        schema_json = schema_to_json(schema)
    except Exception as e:
        conn.close()
        return {"source_id": source_id, "status": "error", "error": str(e)}
    # only save source and cache schema if connector succeeded
    persistence.save_source(conn, source_id, org, name, "csv", cfg)
    persistence.cache_schema(conn, source_id, schema_json)
    _connectors[source_id] = c
    conn.close()
    return {"source_id": source_id, "status": "active", "files": [Path(x).name for x in saved], "tables": schema_json}


@app.post("/api/connect-db")
async def connect_db(request: Request, payload: dict) -> dict:
    org = _org_id(request)
    src_type = payload.get("type", "postgresql")
    name = payload.get("name", src_type)
    if src_type in ("csv", "file"):
        raise HTTPException(400, "Use /api/upload for files")
    if src_type == "sqlite":
        cfg = {"path": payload["path"]}
    elif src_type == "duckdb":
        cfg = {"path": payload["path"]}
    else:
        cfg = {"connection_string": payload["connection_string"], "connect_args": payload.get("connect_args", {})}
    try:
        c = build_connector(src_type, **cfg)
    except Exception as e:
        raise HTTPException(400, f"Connection failed: {e}")
    if not c.test_connection():
        raise HTTPException(400, "Connection test failed")
    source_id = f"src_{uuid.uuid4().hex[:8]}"
    cfg_store = dict(cfg)
    conn = persistence.get_db()
    persistence.save_source(conn, source_id, org, name, src_type, cfg_store)
    schema = c.get_schema()
    persistence.cache_schema(conn, source_id, schema_to_json(schema))
    _connectors[source_id] = c
    conn.close()
    return {"source_id": source_id, "status": "active", "tables": list(schema.keys())}


@app.get("/api/sources")
def sources(request: Request) -> dict:
    conn = persistence.get_db()
    srcs = persistence.list_org_sources(conn, _org_id(request))
    conn.close()
    safe = [{**s, "config_json": "hidden" if "connection_string" in s["config_json"] else s["config_json"]} for s in srcs]
    return {"sources": safe}


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str) -> dict:
    conn = persistence.get_db()
    src = persistence.get_source(conn, source_id)
    if src and src.get("type") in ("csv", "file"):
        shutil.rmtree(UPLOAD_DIR / source_id, ignore_errors=True)
    persistence.delete_source(conn, source_id)
    _connectors.pop(source_id, None)
    conn.close()
    return {"status": "deleted"}


def _load_schema(conn, source_id: str) -> dict[str, TableSchema]:
    schema_json = persistence.get_cache_schema(conn, source_id)
    if not schema_json:
        c = _build_connector(source_id)
        schema = c.get_schema()
        persistence.cache_schema(conn, source_id, schema_to_json(schema))
        return schema
    # rebuild from json
    schema: dict[str, TableSchema] = {}
    from .datasource import ColumnSchema
    for t in schema_json:
        ts = TableSchema(name=t["table"], row_count=t.get("row_count", 0))
        for c in t["columns"]:
            ts.columns.append(ColumnSchema(
                name=c["name"],
                dtype=c.get("dtype", c.get("kind", "object")),
                kind=c["kind"],
                is_pk=c.get("is_pk", False),
                is_fk=c.get("is_fk", False),
                fk_ref_table=c.get("fk_ref_table"),
                fk_ref_column=c.get("fk_ref_column"),
                sample_values=c.get("sample_values", []),
                null_count=c.get("null_count", 0),
                unique_count=c.get("unique_count", 0),
            ))
        schema[t["table"]] = ts
    return schema


@app.get("/api/schema")
def get_schema(source_id: str) -> dict:
    try:
        conn = persistence.get_db()
        schema_json = persistence.get_cache_schema(conn, source_id)
        if not schema_json:
            c = _build_connector(source_id)
            schema = c.get_schema()
            schema_json = schema_to_json(schema)
            persistence.cache_schema(conn, source_id, schema_json)
        conn.close()
        return {"source_id": source_id, "tables": schema_json}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, detail=f"Schema unavailable: {e}")


@app.post("/api/generate-sql")
def api_generate_sql(request: Request, payload: dict) -> dict:
    source_id = payload["source_id"]
    question = payload["question"]
    conn = persistence.get_db()
    schema = _load_schema(conn, source_id)
    conn.close()
    intent = detect_intent(question, schema)
    sqls = generate_sql(question, intent, schema)
    problems = []
    for s in sqls:
        problems.extend(validate_sql(s, schema))
    return {"sql": ";\n".join(sqls), "intent": intent.__dict__, "issues": problems}


@app.post("/api/execute")
def api_execute(request: Request, payload: dict) -> dict:
    source_id = payload["source_id"]
    sql = payload["sql"]
    if not sql.strip():
        raise HTTPException(400, "Empty SQL")
    c = _build_connector(source_id)
    conn = persistence.get_db()
    schema = _load_schema(conn, source_id)
    conn.close()
    problems = validate_sql(sql, schema)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    try:
        rows, t = c.execute(sql)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Execution failed: {e}")
    return {"rows": rows[: settings.max_sql_rows], "count": len(rows), "execution_time": t}


@app.post("/api/chat")
def api_chat(request: Request, payload: dict) -> dict:
    source_id = payload["source_id"]
    question = payload["question"]
    conversation_id = payload.get("conversation_id")
    user_id = "user_default"
    conn = persistence.get_db()
    if not conversation_id:
        conversation_id = persistence.create_conversation(conn, _org_id(request), user_id, source_id, question[:80])
    c = _build_connector(source_id)
    schema = _load_schema(conn, source_id)
    try:
        result = run_pipeline(question, c, schema)
    finally:
        pass
    answer = None
    if result.success:
        insight = generate_insight(question, result)
        chart = select_chart(result)
        followups = generate_followups(question, result)
        answer = {
            "insight": insight,
            "chart": chart,
            "followups": followups,
            "sql": result.sql,
            "rows": _coerce_rows(result.final_rows[: settings.max_sql_rows]),
            "plan": result.plan,
            "execution_time": result.execution_time,
            "retries": result.retries,
        }
        status = "success"
    else:
        answer = {"error": result.error or "Execution failed", "sql": result.sql, "plan": result.plan}
        status = "error"
    persistence.add_message(conn, conversation_id, question, json.dumps(answer, default=str), result.sql, result.execution_time, status)
    conn.close()
    return {"conversation_id": conversation_id, "question": question, "answer": answer}


@app.get("/api/history")
def history(request: Request) -> dict:
    conn = persistence.get_db()
    rows = persistence.get_history(conn, _org_id(request))
    conn.close()
    return {"history": rows}


@app.get("/api/insights/{conversation_id}")
def conversation_insights(conversation_id: str) -> dict:
    conn = persistence.get_db()
    msgs = persistence.list_messages(conn, conversation_id)
    conn.close()
    insights = [m["answer"] for m in msgs if m.get("answer") and "insight" in (m["answer"] or {})]
    return {"conversation_id": conversation_id, "insights": insights}


@app.post("/api/save-dashboard")
def save_dashboard(request: Request, payload: dict) -> dict:
    conn = persistence.get_db()
    did = persistence.save_dashboard(conn, _org_id(request), "user_default", payload["title"], payload["layout"], payload.get("dashboard_id"))
    conn.close()
    return {"dashboard_id": did}


@app.get("/api/dashboards")
def dashboards(request: Request) -> dict:
    conn = persistence.get_db()
    items = persistence.list_dashboards(conn, _org_id(request))
    conn.close()
    return {"dashboards": items}


@app.get("/api/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str) -> dict:
    conn = persistence.get_db()
    d = persistence.get_dashboard(conn, dashboard_id)
    conn.close()
    if not d:
        raise HTTPException(404, "Dashboard not found")
    return d


# ---------- static frontend ----------

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR / "assets")), name="assets")
    index_file = _FRONTEND_DIR / "index.html"

    @app.get("/")
    def root_index() -> FileResponse:
        return FileResponse(str(index_file))

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        # SPA fallback
        target = _FRONTEND_DIR / full_path
        if target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(index_file))
else:
    @app.get("/")
    def root_fallback() -> dict:
        return {
            "message": "DataWhisper backend is running. Build the frontend (cd frontend && npm install && npm run build) to serve the UI.",
            "docs": "/api/docs",
        }
