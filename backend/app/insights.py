"""Insight engine, chart selection, and follow-up suggestions."""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime
from typing import Any

from .llm import get_llm
from .sqlengine import AnalysisResult


def _str(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    numeric = None
    label = None
    for k, v in rows[0].items():
        if isinstance(v, (int, float)) and numeric is None:
            numeric = k
        elif not isinstance(v, (int, float)) and label is None and isinstance(v, str):
            label = k
    if numeric is None:
        return {"row_count": len(rows)}
    vals = [r[numeric] for r in rows if r.get(numeric) is not None]
    s = {
        "measure_col": numeric,
        "label_col": label,
        "total": sum(vals),
        "mean": statistics.mean(vals) if vals else None,
        "min": min(vals) if vals else None,
        "max": max(vals) if vals else None,
        "row_count": len(rows),
    }
    return s


def generate_insight(question: str, result: AnalysisResult) -> str:
    summary = _summarize(result.final_rows)
    payload = json.dumps({
        "question": question,
        "rows": result.final_rows[:10],
        "summary": summary,
        "measure": summary.get("measure_col", "the metric"),
        "dimension": summary.get("label_col"),
    }, default=str)
    llm = get_llm()
    text = llm.chat(
        "You are the INSIGHT ENGINE. Given the data below, produce a concise, "
        "non-numeric-heavy explanation of trends, drivers, and outliers. Max 3 sentences.",
        payload,
    )
    if not text:
        # fallback deterministic
        if not result.final_rows:
            return "No data was returned for this question."
        parts = []
        if summary.get("total") is not None:
            parts.append(f"Total {summary['measure_col']} is {summary['total']:,.2f}.")
        if summary.get("max") is not None and summary.get("label_col"):
            top = max(result.final_rows, key=lambda r: r.get(summary["measure_col"], 0))
            parts.append(f"The leading {summary['label_col']} is {top.get(summary['label_col'])}.")
        sp = summary.get("max", 0) or 0
        lo = summary.get("min", 0) if summary.get("min") is not None else sp
        if sp and lo and sp > lo:
            spread = sp - lo
            parts.append(f"There is notable spread ({spread:,.0f}) across the groups.")
        return " ".join(parts) or "Results returned."
    return text


def select_chart(result: AnalysisResult) -> dict:
    rows = result.final_rows
    if not rows:
        return {"type": "none", "data": [], "config": {}}
    keys = list(rows[0].keys())
    # find label & value columns
    label = None
    value = None
    for k in keys:
        v0 = rows[0][k]
        if isinstance(v0, (int, float)) and value is None:
            value = k
        elif not isinstance(v0, (int, float)) and label is None and (isinstance(v0, str)):
            label = k
    if value is None:
        value = keys[-1]
    if label is None:
        label = keys[0]
    # chart selection
    is_time_label = any(c in str(label).lower() for c in ("day", "month", "week", "quarter", "year", "date"))
    chart_type = "line" if is_time_label else "bar"
    if result.intent.compare:
        chart_type = "grouped_bar"
    if chart_type == "line":
        data = [{"x": _str(r.get(label)), "y": r.get(value)} for r in rows]
    else:
        data = [{"label": _str(r.get(label)), "value": r.get(value)} for r in rows]
    return {
        "type": chart_type,
        "label_col": label,
        "value_col": value,
        "data": data,
        "config": {"title": result.intent.raw or "Result"},
    }


def generate_followups(question: str, result: AnalysisResult) -> list[str]:
    summary = _summarize(result.final_rows)
    payload = json.dumps({
        "measure": summary.get("measure_col"),
        "dimension": summary.get("label_col"),
        "question": question,
    })
    llm = get_llm()
    text = llm.chat("You are the FOLLOWUP SUGGESTION engine. Return JSON of 4 follow-up questions.", payload)
    try:
        return json.loads(text).get("suggestions", [])[:4]
    except Exception:
        m = summary.get("measure_col")
        d = summary.get("label_col")
        return [
            f"Break down {m} by {d}" if m and d else "Break down by another dimension",
            f"Compare {m} across periods" if m else "Compare across periods",
            f"Top {m} entries" if m else "Top entries",
            f"Show anomalies in {m}" if m else "Show anomalies",
        ][:4]
