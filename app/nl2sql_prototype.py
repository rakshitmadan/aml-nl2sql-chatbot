"""Phase 2 prototype: plain-English question -> LLM generates SQL -> we run
it -> print results. No LangGraph, no guardrail node, no policy engine yet
— this just proves the core NL->SQL loop works end to end.

Run with: ./venv/bin/python -m app.nl2sql_prototype "your question here"
"""

import re
import sqlite3
import sys

from anthropic import Anthropic
from dotenv import load_dotenv

from app.schema import SYSTEM_PROMPT

load_dotenv()

DB_PATH = "db/aml.db"
MODEL = "claude-sonnet-4-6"


def generate_sql(question: str) -> str:
    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    raw = response.content[0].text.strip()
    return re.sub(r"^```sql\s*|```\s*$", "", raw, flags=re.MULTILINE).strip()


def run_query(sql: str) -> tuple[list[str], list[sqlite3.Row]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    columns = [d[0] for d in cur.description]
    conn.close()
    return columns, rows


def main():
    question = " ".join(sys.argv[1:]) or "Show me last month's transactions over $10,000"
    print(f"Question: {question}\n")

    sql = generate_sql(question)
    print(f"Generated SQL:\n{sql}\n")

    columns, rows = run_query(sql)
    print(f"Results ({len(rows)} rows):")
    print(columns)
    for row in rows[:20]:
        print(tuple(row))
    if len(rows) > 20:
        print(f"... and {len(rows) - 20} more rows")


if __name__ == "__main__":
    main()
