"""Schema understanding: build a text description of the schema for the LLM."""
from __future__ import annotations

import json
from .datasource import TableSchema


def schema_to_text(schema: dict[str, TableSchema]) -> str:
    """Render the schema as a compact, LLM-friendly text block."""
    lines = []
    for tname, t in schema.items():
        lines.append(f"TABLE {tname} (rows={t.row_count})")
        for col in t.columns:
            extras = []
            if col.is_pk:
                extras.append("PK")
            if col.is_fk:
                extras.append(f"FK->{col.fk_ref_table}.{col.fk_ref_column}")
            extras_str = f" [{', '.join(extras)}]" if extras else ""
            samples = ", ".join(repr(s) for s in col.sample_values[:5])
            lines.append(f"  - {col.name} ({col.kind}){extras_str}  sample: {samples}")
        lines.append("")
    return "\n".join(lines)


def schema_to_json(schema: dict[str, TableSchema]) -> list[dict]:
    out = []
    for tname, t in schema.items():
        out.append({
            "table": tname,
            "row_count": t.row_count,
            "columns": [
                {
                    "name": c.name,
                    "dtype": c.dtype,
                    "kind": c.kind,
                    "is_pk": c.is_pk,
                    "is_fk": c.is_fk,
                    "fk_ref_table": c.fk_ref_table,
                    "fk_ref_column": c.fk_ref_column,
                    "unique_count": c.unique_count,
                    "sample_values": c.sample_values,
                    "null_count": c.null_count,
                }
                for c in t.columns
            ],
        })
    return out
