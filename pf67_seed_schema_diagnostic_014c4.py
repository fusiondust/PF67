#!/usr/bin/env python3
"""
PF67 Read-Only Seed Diagnostic 014C4

Run:
  cd /volume1/docker/pf67
  python pf67_seed_schema_diagnostic_014c4.py

Optional:
  python pf67_seed_schema_diagnostic_014c4.py --db data/db/pf67.sqlite3

Purpose:
- Read-only diagnostic for sample Protocol/Procedure seeding.
- Does not modify the database.
- Does not restart Docker.
- Dumps the exact schema for:
    protocol_templates
    step_templates
    jobs
    job_steps
- Shows sample rows and counts.
- Helps identify:
    - protocol/procedure flag column
    - public/private flag column
    - rich text / instruction columns
    - timing window columns
    - parent/foreign-key relationship between protocol_templates and step_templates
- Produces a text report that can be pasted back into ChatGPT.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def find_root() -> Path:
    cwd = Path.cwd().resolve()

    for candidate in [cwd, cwd.parent, Path("/volume1/docker/pf67")]:
        if (candidate / "app").is_dir() or (candidate / "data").is_dir():
            return candidate

    print("ERROR: Could not find PF67 root. Run from /volume1/docker/pf67.")
    sys.exit(1)


ROOT = find_root()
REPORT = ROOT / ("pf67_seed_schema_diagnostic_014c4_" + STAMP + ".txt")


def choose_db(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)

        if not path.is_absolute():
            path = ROOT / path

        if not path.exists():
            print("ERROR: DB not found:", path)
            sys.exit(1)

        return path

    preferred = ROOT / "data" / "db" / "pf67.sqlite3"

    if preferred.exists():
        return preferred

    candidates = []
    for pattern in ["*.db", "*.sqlite", "*.sqlite3"]:
        candidates.extend(ROOT.rglob(pattern))

    candidates = [
        path for path in candidates
        if path.is_file()
        and "_pf67_patch_backups" not in path.parts
        and ".git" not in path.parts
        and path.stat().st_size > 0
    ]

    if not candidates:
        print("ERROR: No SQLite database found. Pass --db.")
        sys.exit(1)

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone()
    return bool(row)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_info(conn: sqlite3.Connection, table: str):
    return conn.execute("pragma table_info(" + q(table) + ")").fetchall()


def foreign_keys(conn: sqlite3.Connection, table: str):
    return conn.execute("pragma foreign_key_list(" + q(table) + ")").fetchall()


def indexes(conn: sqlite3.Connection, table: str):
    return conn.execute("pragma index_list(" + q(table) + ")").fetchall()


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute("select count(*) from " + q(table)).fetchone()[0])


def sample_rows(conn: sqlite3.Connection, table: str, limit: int = 3):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("select * from " + q(table) + " limit ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


def safe_value(value):
    if value is None:
        return None

    value = str(value)

    if len(value) > 700:
        return value[:700] + " ... [truncated]"

    return value


def summarize_rows(rows):
    cleaned = []
    for row in rows:
        cleaned.append({key: safe_value(value) for key, value in row.items()})
    return cleaned


def possible_columns(columns, words):
    hits = []
    for col in columns:
        lower = col.lower()
        if any(word in lower for word in words):
            hits.append(col)
    return hits


def distinct_values(conn, table, column, limit=20):
    try:
        rows = conn.execute(
            "select distinct " + q(column) + " from " + q(table) + " order by " + q(column) + " limit ?",
            (limit,),
        ).fetchall()
        return [safe_value(row[0]) for row in rows]
    except Exception as exc:
        return ["ERROR: " + str(exc)]


def text_lengths(conn, table, column, limit=5):
    try:
        rows = conn.execute(
            "select rowid, length(" + q(column) + "), substr(" + q(column) + ", 1, 220) "
            "from " + q(table) + " "
            "where " + q(column) + " is not null and length(" + q(column) + ") > 0 "
            "order by length(" + q(column) + ") desc limit ?",
            (limit,),
        ).fetchall()
        return [
            {
                "rowid": row[0],
                "length": row[1],
                "sample": safe_value(row[2]),
            }
            for row in rows
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


def find_relationship_guess(conn, parent_table, step_table):
    if not (table_exists(conn, parent_table) and table_exists(conn, step_table)):
        return []

    parent_cols = [row[1] for row in table_info(conn, parent_table)]
    step_cols = [row[1] for row in table_info(conn, step_table)]

    guesses = []
    candidates = [
        "protocol_template_id",
        "template_id",
        "protocol_id",
        "parent_id",
        "template_ref_id",
        "protocol_template",
    ]

    parent_id_candidates = [
        "id",
        "protocol_template_id",
        "template_id",
        "protocol_id",
    ]

    for step_col in candidates:
        if step_col not in step_cols:
            continue

        for parent_col in parent_id_candidates:
            if parent_col not in parent_cols:
                continue

            try:
                matched = conn.execute(
                    "select count(*) from " + q(step_table) + " s "
                    "join " + q(parent_table) + " p "
                    "on s." + q(step_col) + " = p." + q(parent_col)
                ).fetchone()[0]
                non_null = conn.execute(
                    "select count(*) from " + q(step_table) + " where " + q(step_col) + " is not null"
                ).fetchone()[0]
                guesses.append({
                    "step_column": step_col,
                    "parent_column": parent_col,
                    "matching_steps": int(matched),
                    "non_null_steps": int(non_null),
                })
            except Exception:
                pass

    guesses.sort(key=lambda item: (item["matching_steps"], item["non_null_steps"]), reverse=True)
    return guesses


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", help="Path to SQLite DB. Defaults to data/db/pf67.sqlite3 when present.")
    args = parser.parse_args()

    db = choose_db(args.db)
    conn = sqlite3.connect(str(db))

    all_tables = [
        row[0] for row in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"
        ).fetchall()
    ]

    target_tables = [
        "protocol_templates",
        "step_templates",
        "jobs",
        "job_steps",
        "job_notes",
        "attachments",
        "protocol_attachments",
    ]

    lines = []
    lines.append("PF67 Seed Schema Diagnostic 014C4")
    lines.append("Root: " + str(ROOT))
    lines.append("Database: " + str(db))
    lines.append("Timestamp: " + STAMP)
    lines.append("")
    lines.append("=== All tables ===")
    lines.extend("- " + table for table in all_tables)
    lines.append("")

    for table in target_tables:
        lines.append("=== " + table + " ===")

        if not table_exists(conn, table):
            lines.append("MISSING")
            lines.append("")
            continue

        info = table_info(conn, table)
        cols = [row[1] for row in info]
        lines.append("Count: " + str(count_rows(conn, table)))
        lines.append("")
        lines.append("Columns:")
        for row in info:
            lines.append(
                "  cid={cid} name={name} type={type} notnull={notnull} default={default} pk={pk}".format(
                    cid=row[0],
                    name=row[1],
                    type=row[2],
                    notnull=row[3],
                    default=row[4],
                    pk=row[5],
                )
            )

        lines.append("")
        lines.append("Foreign keys:")
        fks = foreign_keys(conn, table)
        if fks:
            for row in fks:
                lines.append("  " + repr(tuple(row)))
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("Indexes:")
        idxs = indexes(conn, table)
        if idxs:
            for row in idxs:
                lines.append("  " + repr(tuple(row)))
        else:
            lines.append("  (none)")

        lines.append("")
        lines.append("Likely title/name columns: " + ", ".join(possible_columns(cols, ["title", "name", "label"])) )
        lines.append("Likely instruction/content columns: " + ", ".join(possible_columns(cols, ["instruction", "description", "content", "body", "note", "html", "detail", "wysiwyg"])) )
        lines.append("Likely type/procedure columns: " + ", ".join(possible_columns(cols, ["type", "kind", "procedure", "protocol", "template", "mode"])) )
        lines.append("Likely public/visibility columns: " + ", ".join(possible_columns(cols, ["public", "visibility", "visible", "private"])) )
        lines.append("Likely timing columns: " + ", ".join(possible_columns(cols, ["minute", "check", "ideal", "limit", "risk", "failure", "fail", "wait", "duration"])) )
        lines.append("Likely parent columns: " + ", ".join(possible_columns(cols, ["protocol_template", "template_id", "protocol_id", "parent"])) )

        lines.append("")
        lines.append("Distinct values for likely type/procedure/public columns:")
        for col in possible_columns(cols, ["type", "kind", "procedure", "protocol", "template", "mode", "public", "visibility", "visible", "private"]):
            vals = distinct_values(conn, table, col)
            lines.append("  " + col + ": " + json.dumps(vals, ensure_ascii=False))

        lines.append("")
        lines.append("Longest text samples from likely instruction/content columns:")
        for col in possible_columns(cols, ["instruction", "description", "content", "body", "note", "html", "detail", "wysiwyg"]):
            lines.append("  " + col + ":")
            for item in text_lengths(conn, table, col):
                lines.append("    " + json.dumps(item, ensure_ascii=False))

        lines.append("")
        lines.append("Sample rows:")
        for row in summarize_rows(sample_rows(conn, table, limit=3)):
            lines.append(json.dumps(row, indent=2, ensure_ascii=False))

        lines.append("")

    lines.append("=== Relationship guesses: protocol_templates -> step_templates ===")
    for guess in find_relationship_guess(conn, "protocol_templates", "step_templates"):
        lines.append(json.dumps(guess, ensure_ascii=False))
    lines.append("")

    lines.append("=== Existing Sample records ===")
    if table_exists(conn, "protocol_templates"):
        cols = [row[1] for row in table_info(conn, "protocol_templates")]
        title_col = None
        for candidate in ["title", "name", "template_name", "protocol_name", "label"]:
            if candidate in cols:
                title_col = candidate
                break

        if title_col:
            rows = conn.execute(
                "select rowid, " + q(title_col) + " from protocol_templates where " + q(title_col) + " like 'Sample %' order by rowid"
            ).fetchall()
            if rows:
                for row in rows:
                    lines.append("rowid=" + str(row[0]) + " title=" + str(row[1]))
            else:
                lines.append("(none)")
        else:
            lines.append("Could not identify title column.")

    conn.close()

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("Wrote", REPORT)
    print("")
    print("Please upload or paste this report:")
    print(REPORT)


if __name__ == "__main__":
    main()
