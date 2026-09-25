"""SQL safety checks that run between "LLM generated this" and "we actually
execute it." Nothing here trusts the model — every check is either a hard
parse-level fact (is this even a single SELECT?) or checked against the
*real* schema pulled from `db/models.py`, not the prompt's schema
description (which the model could ignore or the prompt could drift from).
"""

import time
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from db.models import Base

DIALECT = "sqlite"
ROW_LIMIT = 5000
QUERY_TIMEOUT_SECONDS = 5


def get_schema() -> dict[str, dict[str, str]]:
    """Ground-truth table/column map pulled straight from the SQLAlchemy
    models, so the guardrail can never drift out of sync with the real
    database the way a second hand-written copy could."""
    return {
        table.name: {col.name: str(col.type) for col in table.columns}
        for table in Base.metadata.tables.values()
    }


def validate_sql(sql: str) -> tuple[str | None, list[str], Optional[int]]:
    """Returns (safe_sql, errors, effective_row_limit). safe_sql is None if
    any check failed. On success, safe_sql has a row limit injected if one
    wasn't already present, and effective_row_limit tells the caller what
    that cap actually is — needed downstream to detect truncation (if a
    query returns exactly `effective_row_limit` rows, there may be more
    matching data that got silently cut off).
    """
    errors: list[str] = []

    try:
        statements = sqlglot.parse(sql, read=DIALECT)
    except Exception as e:
        return None, [f"SQL failed to parse: {e}"], None

    if len(statements) != 1:
        return None, ["Only a single SQL statement is allowed."], None

    parsed = statements[0]
    if parsed is None or not isinstance(parsed, exp.Select):
        return None, ["Only SELECT statements are allowed."], None

    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create)
    if any(parsed.find(node_type) for node_type in forbidden):
        errors.append("Query contains a forbidden write/DDL operation.")

    schema = get_schema()
    referenced_tables = {table.name for table in parsed.find_all(exp.Table)}
    unknown_tables = referenced_tables - schema.keys()
    if unknown_tables:
        # Checked explicitly: sqlglot's qualify() only validates *columns*
        # against the schema — a `SELECT * FROM nonexistent_table` has no
        # columns to resolve, so qualify() silently lets it through.
        errors.append(f"Unknown table(s) referenced: {', '.join(sorted(unknown_tables))}")

    if not unknown_tables:
        try:
            qualify(parsed.copy(), schema=schema, dialect=DIALECT)
        except Exception as e:
            errors.append(f"Unknown column referenced: {e}")

    if errors:
        return None, errors, None

    if not parsed.args.get("limit"):
        parsed = parsed.limit(ROW_LIMIT)

    limit_node = parsed.args.get("limit")
    effective_limit = int(limit_node.expression.this) if limit_node else None

    return parsed.sql(dialect=DIALECT), [], effective_limit


def make_timeout_handler(deadline: float):
    def handler():
        return time.monotonic() > deadline

    return handler
