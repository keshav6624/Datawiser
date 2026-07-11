"""Data source connectors and registry.

Each connector provides:
  - test_connection(): bool
  - get_schema(): dict[str, TableSchema]
  - execute(sql: str, params) -> list[dict]

Connections are read-only by design.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .config import UPLOAD_DIR


@dataclass
class ColumnSchema:
    name: str
    dtype: str  # pandas dtype string
    kind: str  # categorical | numeric | temporal | boolean
    is_pk: bool = False
    is_fk: bool = False
    fk_ref_table: str | None = None
    fk_ref_column: str | None = None
    sample_values: list[Any] = field(default_factory=list)
    null_count: int = 0
    unique_count: int = 0


@dataclass
class TableSchema:
    name: str
    row_count: int
    columns: list[ColumnSchema] = field(default_factory=list)


# ---------- helpers ----------

_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_column_names(names: list[str]) -> list[str]:
    """Produce a list of valid, unique SQL column identifiers."""
    replacements = {
        "$": "dollar", "%": "pct", "#": "num", "@": "at",
        "&": "and", "|": "or", "<": "lt", ">": "gt", "=": "eq",
        "+": "plus", "*": "star", "/": "per", "\\": "per",
        "(": "", ")": "", "[": "", "]": "", "{": "", "}": "",
        ":": "", ";": "", "'": "", '"': "", ",": "", ".": "_dot_",
        "?": "", "!": "", "~": "", "`": "", "^": "",
    }
    sanitized: list[str] = []
    seen: set[str] = set()
    for i, n in enumerate(names):
        s = n.strip().replace(" ", "_").replace("-", "_")
        for old, new in replacements.items():
            s = s.replace(old, new)
        s = _SANITIZE_RE.sub("", s)
        if not s or s[0].isdigit():
            s = f"col_{i}"
        # deduplicate
        base = s
        counter = 1
        while s in seen:
            s = f"{base}_{counter}"
            counter += 1
        sanitized.append(s)
        seen.add(s)
    return sanitized

def _classify(dtype: str, series: pd.Series) -> str:
    s = series.dtype
    if pd.api.types.is_numeric_dtype(s):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "temporal"
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    # Heuristic: object columns that look like dates are temporal
    if dtype == "object":
        sample = series.dropna().head(50)
        if len(sample) > 0:
            parsed = pd.to_datetime(sample, errors="coerce")
            if parsed.notna().mean() > 0.8:
                return "temporal"
    return "categorical"


def _infer_fks(tables: dict[str, TableSchema]) -> None:
    """Cheap FK inference: a column named '<x>_id' pointing at table <x>."""
    table_names = {t.lower(): t for t in tables}
    for tname, t in tables.items():
        for col in t.columns:
            if not col.name.lower().endswith("_id"):
                continue
            base = col.name[:-3].lower()
            if base + "s" in table_names:
                ref = table_names[base + "s"]
            elif base in table_names:
                ref = table_names[base]
            else:
                continue
            ref_table = tables[ref]
            # does the referenced table have an id column?
            ref_id_col = next((c for c in ref_table.columns if c.name.lower() == "id"), None)
            if ref_id_col is not None:
                col.is_fk = True
                col.fk_ref_table = ref
                col.fk_ref_column = ref_id_col.name
    # PK inference: a column named 'id' or '<table_singular>_id'
    for tname, t in tables.items():
        singular = tname.rstrip("s").lower()
        for col in t.columns:
            if col.name.lower() == "id" or col.name.lower() == f"{singular}_id" or col.name.lower() == f"{tname.lower()}_id":
                col.is_pk = True
                break


# ---------- base connector ----------

class BaseConnector:
    name: str = "base"

    def test_connection(self) -> bool:
        raise NotImplementedError

    def get_schema(self) -> dict[str, TableSchema]:
        raise NotImplementedError

    def execute(self, sql: str, params: dict | None = None) -> tuple[list[dict], float]:
        raise NotImplementedError


# ---------- CSV / file connector (loads into an in-memory DuckDB) ----------

class FileConnector(BaseConnector):
    name = "file"

    def __init__(self, source_id: str, files: list[Path]) -> None:
        import duckdb
        self._con = duckdb.connect(":memory:")
        self._duckdb = duckdb
        self._source_id = source_id
        self._files = files
        self._tables: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        for f in self._files:
            if not f.exists():
                continue
            table_name = _SANITIZE_RE.sub("", f.stem.lower().replace(" ", "_").replace("-", "_"))
            df = pd.read_csv(f) if f.suffix.lower() == ".csv" else pd.read_excel(f)
            df.columns = _sanitize_column_names(list(df.columns))
            # Auto-parse columns that look like dates so DuckDB types them as
            # TIMESTAMP/DATE (otherwise date_trunc breaks).
            for col in df.columns:
                if df[col].dtype == object:
                    parsed = pd.to_datetime(df[col], errors="coerce")
                    if parsed.notna().mean() > 0.8:
                        df[col] = parsed
            self._con.register(f"df_{table_name}", df)
            self._con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM df_{table_name}')
            self._tables[table_name] = table_name

    def test_connection(self) -> bool:
        try:
            self._con.execute("SELECT 1").fetchall()
            return True
        except Exception:
            return False

    def get_schema(self) -> dict[str, TableSchema]:
        tables: dict[str, TableSchema] = {}
        has_info_schema = self._has_information_schema()
        for tname in self._tables:
            if has_info_schema:
                try:
                    cols_info = self._con.execute(
                        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [tname]
                    ).fetchall()
                except Exception:
                    cols_info = self._fallback_columns(tname)
            else:
                cols_info = self._fallback_columns(tname)
            count = self._con.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            t = TableSchema(name=tname, row_count=count)
            for cname, ctype in cols_info:
                series = self._con.execute(f'SELECT "{cname}" FROM "{tname}" LIMIT 1000').fetch_df()[cname]
                kind = _classify(str(series.dtype), series)
                t.columns.append(ColumnSchema(
                    name=cname,
                    dtype=str(series.dtype),
                    kind=kind,
                    sample_values=[_coerce(v) for v in series.dropna().unique().tolist()[:5]],
                    null_count=int(series.isna().sum()),
                    unique_count=int(series.nunique(dropna=True)),
                ))
            tables[tname] = t
        _infer_fks(tables)
        return tables

    def _has_information_schema(self) -> bool:
        try:
            self._con.execute("SELECT 1 FROM information_schema.columns LIMIT 1").fetchall()
            return True
        except Exception:
            return False

    def _fallback_columns(self, tname: str) -> list[tuple[str, str]]:
        desc = self._con.execute(f'DESCRIBE "{tname}"').fetchall()
        return [(row[0], row[1]) for row in desc]

    def execute(self, sql: str, params: dict | None = None) -> tuple[list[dict], float]:
        import time
        start = time.time()
        cur = self._con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows, time.time() - start


def _coerce(v: Any) -> Any:
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    if isinstance(v, bytes):
        return "<binary>"
    return v


# ---------- SQLite connector ----------

class SQLiteConnector(BaseConnector):
    name = "sqlite"

    def __init__(self, path: str) -> None:
        self._path = path
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    def test_connection(self) -> bool:
        try:
            self._con.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def get_schema(self) -> dict[str, TableSchema]:
        tables_meta = self._con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        tables: dict[str, TableSchema] = {}
        for (tname,) in tables_meta:
            df = pd.read_sql_query(f'SELECT * FROM "{tname}" LIMIT 1000', self._con)
            count_row = self._con.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()
            count = count_row[0] if count_row else 0
            t = TableSchema(name=tname, row_count=count)
            for cname in df.columns:
                series = df[cname]
                kind = _classify(str(series.dtype), series)
                t.columns.append(ColumnSchema(
                    name=cname,
                    dtype=str(series.dtype),
                    kind=kind,
                    sample_values=[_coerce(v) for v in series.dropna().unique().tolist()[:5]],
                    null_count=int(series.isna().sum()),
                    unique_count=int(series.nunique(dropna=True)),
                ))
            tables[tname] = t
        _infer_fks(tables)
        return tables

    def execute(self, sql: str, params: dict | None = None) -> tuple[list[dict], float]:
        import time
        start = time.time()
        cur = self._con.execute(sql, params or {})
        rows = [dict(r) for r in cur.fetchall()]
        return rows, time.time() - start


# ---------- DuckDB file connector ----------

class DuckDBConnector(BaseConnector):
    name = "duckdb"

    def __init__(self, path: str) -> None:
        import duckdb
        self._con = duckdb.connect(path, read_only=True)

    def test_connection(self) -> bool:
        try:
            self._con.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def get_schema(self) -> dict[str, TableSchema]:
        tables_meta = self._con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
        tables: dict[str, TableSchema] = {}
        for (tname,) in tables_meta:
            df = self._con.execute(f'SELECT * FROM "{tname}" LIMIT 1000').fetch_df()
            count = self._con.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]
            t = TableSchema(name=tname, row_count=count)
            for cname in df.columns:
                series = df[cname]
                kind = _classify(str(series.dtype), series)
                t.columns.append(ColumnSchema(
                    name=cname, dtype=str(series.dtype), kind=kind,
                    sample_values=[_coerce(v) for v in series.dropna().unique().tolist()[:5]],
                    null_count=int(series.isna().sum()), unique_count=int(series.nunique(dropna=True)),
                ))
            tables[tname] = t
        _infer_fks(tables)
        return tables

    def execute(self, sql: str, params: dict | None = None) -> tuple[list[dict], float]:
        import time
        start = time.time()
        cur = self._con.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows, time.time() - start


# ---------- PostgreSQL / MySQL / Snowflake / BigQuery via SQLAlchemy ----------

class SQLAlchemyConnector(BaseConnector):
    name = "sqlalchemy"

    def __init__(self, connection_string: str, connect_args: dict | None = None) -> None:
        from sqlalchemy import create_engine
        self._engine = create_engine(connection_string, connect_args=connect_args or {}, pool_pre_ping=True)
        with self._engine.connect() as _:
            pass

    def test_connection(self) -> bool:
        try:
            with self._engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return True
        except Exception:
            return False

    def get_schema(self) -> dict[str, TableSchema]:
        from sqlalchemy import inspect
        insp = inspect(self._engine)
        tables: dict[str, TableSchema] = {}
        for tname in insp.get_table_names():
            df = pd.read_sql_query(f'SELECT * FROM "{tname}" LIMIT 1000', self._engine)
            count_row = pd.read_sql_query(f'SELECT COUNT(*) AS c FROM "{tname}"', self._engine).iloc[0]
            count = int(count_row["c"]) if not count_row.empty else 0
            t = TableSchema(name=tname, row_count=count)
            pks = set()
            try:
                pk = insp.get_pk_constraint(tname)
                pks = set(pk.get("pk_columns", []))
            except Exception:
                pass
            for cname in df.columns:
                series = df[cname]
                kind = _classify(str(series.dtype), series)
                col = ColumnSchema(
                    name=cname, dtype=str(series.dtype), kind=kind,
                    sample_values=[_coerce(v) for v in series.dropna().unique().tolist()[:5]],
                    null_count=int(series.isna().sum()), unique_count=int(series.nunique(dropna=True)),
                    is_pk=(cname in pks),
                )
                t.columns.append(col)
            tables[tname] = t
        _infer_fks(tables)
        return tables

    def execute(self, sql: str, params: dict | None = None) -> tuple[list[dict], float]:
        import time
        start = time.time()
        df = pd.read_sql_query(sql, self._engine, params=params)
        rows = df.where(pd.notnull(df), None).to_dict(orient="records")
        for r in rows:
            for k, v in list(r.items()):
                r[k] = _coerce(v) if v is not None else None
        return rows, time.time() - start


# ---------- registry ----------

def build_connector(source_type: str, **kwargs) -> BaseConnector:
    source_type = source_type.lower()
    if source_type in ("csv", "excel", "file"):
        files = [Path(f) for f in kwargs["files"]]
        return FileConnector(kwargs["source_id"], files)
    if source_type == "sqlite":
        return SQLiteConnector(kwargs["path"])
    if source_type == "duckdb":
        return DuckDBConnector(kwargs["path"])
    if source_type in ("postgresql", "mysql", "snowflake", "bigquery", "sqlalchemy"):
        return SQLAlchemyConnector(kwargs["connection_string"], kwargs.get("connect_args"))
    raise ValueError(f"Unsupported data source type: {source_type}")
