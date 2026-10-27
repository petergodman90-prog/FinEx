"""Helpers for running plain-SQL migrations from Alembic.

Person A's financial-core migration is maintained as SQL under
``db/migrations/``.  Alembic executes it as part of the single migration
stream, so the SQL has to be split into individual statements: PostgreSQL's
extended query protocol (used by asyncpg) does not accept multiple commands in
one prepared statement.

The splitter understands the constructs used by that file: single-quoted
strings (including ``''`` escapes), dollar-quoted function bodies, line
comments and block comments.
"""

from __future__ import annotations

import re

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into executable statements.

    Semicolons inside strings, dollar-quoted bodies and comments are ignored.
    Empty statements are dropped.  Raises ``ValueError`` for an unterminated
    dollar quote or block comment so a malformed migration fails loudly.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    state = "normal"
    dollar_tag = ""

    while i < n:
        ch = sql[i]

        if state == "normal":
            if ch == "'":
                state = "single"
                buf.append(ch)
                i += 1
            elif ch == "-" and i + 1 < n and sql[i + 1] == "-":
                state = "line_comment"
                buf.append("--")
                i += 2
            elif ch == "/" and i + 1 < n and sql[i + 1] == "*":
                state = "block_comment"
                buf.append("/*")
                i += 2
            elif ch == "$":
                match = _DOLLAR_TAG.match(sql, i)
                if match:
                    dollar_tag = match.group(0)
                    state = "dollar"
                    buf.append(dollar_tag)
                    i += len(dollar_tag)
                else:
                    buf.append(ch)
                    i += 1
            elif ch == ";":
                statement = "".join(buf).strip()
                if statement:
                    statements.append(statement)
                buf = []
                i += 1
            else:
                buf.append(ch)
                i += 1

        elif state == "single":
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                else:
                    state = "normal"
                    i += 1
            else:
                i += 1

        elif state == "dollar":
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                state = "normal"
            else:
                buf.append(ch)
                i += 1

        elif state == "line_comment":
            buf.append(ch)
            if ch == "\n":
                state = "normal"
            i += 1

        elif state == "block_comment":
            if ch == "*" and i + 1 < n and sql[i + 1] == "/":
                buf.append("*/")
                i += 2
                state = "normal"
            else:
                buf.append(ch)
                i += 1

    if state == "dollar":
        raise ValueError("unterminated dollar-quoted string in SQL migration")
    if state == "block_comment":
        raise ValueError("unterminated block comment in SQL migration")

    statement = "".join(buf).strip()
    if statement:
        statements.append(statement)
    return statements
