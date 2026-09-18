"""Scrub OpenAI key patterns from the Puku editor's local embedding DB.

This file is a one-off remediation script. It:
1. Opens .puku/puku-embeddings.db with a short busy_timeout
2. Iterates every text-typed column of every table
3. Replaces any string that looks like an OpenAI key with a safe placeholder
4. CHECKPOINTs and VACUUMs the WAL back into the main DB

The script is safe to run while Puku editor is open; SQLite serializes
writers and the busy_timeout gives it a moment to wait.

After this completes, the WAL file .puku/puku-embeddings.db-wal should
contain no key patterns.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(".puku/puku-embeddings.db")

PATTERNS = [
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
]

PLACEHOLDER = "[REDACTED-BY-GRIDWISE-CLEANUP]"


def scrub(text: str) -> tuple[str, bool]:
    out = text
    changed = False
    for pat in PATTERNS:
        new = pat.sub(PLACEHOLDER, out)
        if new != out:
            changed = True
            out = new
    return out, changed


def main() -> int:
    if not DB_PATH.exists():
        print(f"no DB at {DB_PATH}")
        return 0
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    except sqlite3.OperationalError as exc:
        print(f"could not open DB (likely held exclusively): {exc}")
        return 2
    try:
        cur = conn.cursor()
        # Vacuum the WAL into the main DB so we don't lose work, then write.
        # We can scrub in-place; SQLite will rewrite pages on commit.
        total_changed_cells = 0
        scanned_tables = 0
        # Find all tables.
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = cur.fetchall()
        for name, _sql in tables:
            scanned_tables += 1
            # Discover columns.
            cur.execute(f"PRAGMA table_info({name})")
            cols_info = cur.fetchall()
            text_cols = [row[1] for row in cols_info if row[2].upper() in ("TEXT", "VARCHAR", "BLOB") or row[2] == ""]
            if not text_cols:
                continue
            # Pull all rows.
            try:
                col_list = ", ".join(f'"{c}"' for c in text_cols)
                cur.execute(f"SELECT rowid, {col_list} FROM {name}")
                rows = cur.fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                rid = row[0]
                values = row[1:]
                new_values = []
                row_changed = False
                for v in values:
                    if isinstance(v, str):
                        nv, ch = scrub(v)
                        new_values.append(nv)
                        row_changed = row_changed or ch
                    elif isinstance(v, (bytes, bytearray)):
                        try:
                            decoded = bytes(v).decode("utf-8", errors="ignore")
                            nd, ch = scrub(decoded)
                            new_values.append(v if not ch else nd.encode("utf-8"))
                            row_changed = row_changed or ch
                        except Exception:
                            new_values.append(v)
                    else:
                        new_values.append(v)
                if row_changed:
                    set_clause = ", ".join(f'"{c}" = ?' for c in text_cols)
                    cur.execute(f'UPDATE {name} SET {set_clause} WHERE rowid = ?', (*new_values, rid))
                    total_changed_cells += 1
        conn.commit()
        # Drain the WAL into the main DB so subsequent secret scans
        # see only the rewritten content.
        try:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        try:
            cur.execute("VACUUM")
        except sqlite3.Error:
            pass
        print(f"scanned {scanned_tables} table(s); rewrote {total_changed_cells} row(s) in {DB_PATH}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
