#!/usr/bin/env python
"""TICKET-2: guard against integer literals in Boolean columns inside raw SQL.

Production runs PostgreSQL, which rejects `INSERT INTO t (flag) SELECT 0` with
`DatatypeMismatch: column "flag" is of type boolean but expression is of type
integer`. SQLite casts the same statement silently, so a migration written and
tested locally on SQLite passes and then fails on the server.

Run from the repo root:

    python scripts/check_migrations.py

Exits 0 when clean, 1 with a report when something looks wrong.

This is a linter, not a SQL parser. It matches column names positionally in
`INSERT ... (cols) SELECT|VALUES exprs` and looks for `SET col = 0|1`. It will
NOT catch a boolean value produced by a subquery, a CASE expression, or a
statement built by string concatenation. A clean run is not a substitute for
running `flask db upgrade` against a real PostgreSQL database.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations" / "versions"
MODELS = ROOT / "app" / "models"

# sa.Column('is_archived', sa.Boolean() ...  /  db.Column(db.Boolean ...
SA_BOOL_COL = re.compile(r"""sa\.Column\(\s*['"](\w+)['"]\s*,\s*sa\.Boolean""")
DB_BOOL_COL = re.compile(r"""^\s*(\w+)\s*=\s*db\.Column\(\s*db\.Boolean""", re.MULTILINE)

# conn.execute(sa.text("""...""")) / op.execute("...") — triple- and single-quoted
RAW_SQL = re.compile(
    r"""(?:op\.execute|conn\.execute)\s*\(\s*(?:sa\.text\s*\(\s*)?"""
    r'''(?P<q>"""|\'\'\'|"|\')(?P<sql>.*?)(?P=q)''',
    re.DOTALL,
)

INSERT_COLS = re.compile(
    r"INSERT\s+INTO\s+\w+\s*\((?P<cols>[^)]*)\)\s*(?:SELECT|VALUES\s*\()(?P<exprs>.*?)(?:\bFROM\b|$)",
    re.IGNORECASE | re.DOTALL,
)
SET_ASSIGN = re.compile(r"\bSET\b(?P<body>.*?)(?:\bWHERE\b|$)", re.IGNORECASE | re.DOTALL)

INT_LITERAL = re.compile(r"^[01]$")


def split_top_level(text: str) -> list[str]:
    """Split on commas that are not nested inside parentheses or quotes."""
    parts, depth, buf, quote = [], 0, [], None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def collect_boolean_columns() -> set[str]:
    names: set[str] = set()
    for path in list(MIGRATIONS.glob("*.py")) + list(MODELS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        names.update(SA_BOOL_COL.findall(text))
        names.update(DB_BOOL_COL.findall(text))
    return names


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def check_file(path: Path, bool_cols: set[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    for m in RAW_SQL.finditer(text):
        sql = m.group("sql")
        line = line_of(text, m.start())

        for ins in INSERT_COLS.finditer(sql):
            cols = split_top_level(ins.group("cols"))
            exprs = split_top_level(ins.group("exprs"))
            if len(cols) != len(exprs):
                continue  # column/expression counts disagree — don't guess
            for col, expr in zip(cols, exprs):
                col = col.strip().strip('"')
                if col in bool_cols and INT_LITERAL.match(expr.strip()):
                    problems.append(
                        f"{path.name}:{line}: boolean column '{col}' receives "
                        f"integer literal '{expr.strip()}' — use true/false"
                    )

        for st in SET_ASSIGN.finditer(sql):
            for assign in split_top_level(st.group("body")):
                if "=" not in assign:
                    continue
                col, _, value = assign.partition("=")
                col = col.strip().strip('"').split(".")[-1]
                if col in bool_cols and INT_LITERAL.match(value.strip()):
                    problems.append(
                        f"{path.name}:{line}: boolean column '{col}' set to "
                        f"integer literal '{value.strip()}' — use true/false"
                    )

    return problems


def main() -> int:
    bool_cols = collect_boolean_columns()
    if not bool_cols:
        print("check_migrations: no Boolean columns found — is the repo layout right?")
        return 1

    problems: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.py")):
        problems.extend(check_file(path, bool_cols))

    if problems:
        print("PostgreSQL boolean type mismatches found:\n")
        for p in problems:
            print(f"  {p}")
        print(
            "\nPostgreSQL rejects integer literals in boolean columns "
            "(SQLite accepts them, which is why this slips through locally).\n"
            "Use true/false in raw SQL, or sa.true()/sa.false() in alembic."
        )
        return 1

    print(f"check_migrations: OK — {len(bool_cols)} boolean columns, no integer literals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
