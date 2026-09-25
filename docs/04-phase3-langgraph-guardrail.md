# Phase 3 — LangGraph Wrapping + SQL Guardrail

## Files created

- [`app/guardrail.py`](../app/guardrail.py) — the safety checks
- [`app/graph.py`](../app/graph.py) — the actual LangGraph state machine
- [`tests/test_graph_repair_loop.py`](../tests/test_graph_repair_loop.py) — proves the retry logic works

## LangGraph, concretely

`app/graph.py` builds a graph with three nodes and one conditional edge:

```
[generate_sql] -> [validate_sql] --fails, retries left--> back to [generate_sql]
                        |
                   passes  |  exhausted retries
                        v  v
                 [execute_sql]   END (validation_errors still set)
```

- **`GraphState`** (`TypedDict`) is the shared data every node reads/writes:
  `user_query`, `generated_sql`, `safe_sql`, `validation_errors`,
  `retry_count`, `columns`, `rows`, `error`. Nodes return a partial dict of
  updates; LangGraph merges it into the running state — a node doesn't need
  to know about fields it doesn't touch.
- **`route_after_validation`** is the conditional edge: after
  `validate_sql`, it inspects `validation_errors` and `retry_count` and
  returns `"execute"`, `"retry"`, or `"fail"` — which the graph maps to
  the next node (or `END`).
- On a retry, `generate_sql_node` doesn't just re-ask the same question — it
  sends the model its own previous (bad) SQL plus the specific validation
  errors, and asks it to fix them. That's a materially better retry than
  "try again blind."

## The guardrail (`app/guardrail.py`)

Four checks, all against **ground truth**, not the prompt:

1. **Parses as exactly one statement** (via `sqlglot`) — blocks stacked-query
   injection like `SELECT ...; DROP TABLE customers;`
2. **Is a `SELECT`** — blocks any `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`CREATE`,
   checked at the AST level (not a keyword regex, which is easy to evade)
3. **Every table/column actually exists** — checked against a schema pulled
   live from `db/models.py` (`Base.metadata.tables`), not a second
   hand-maintained list that could drift out of sync with the real database
4. **Row limit injected** if the query doesn't already have one (`LIMIT 500`)

Plus a **query timeout guard** in `execute_sql_node` (`app/graph.py`) using
SQLite's `set_progress_handler` — it aborts a query if it's still running
past a wall-clock deadline (5s default), independent of the guardrail checks
above (this protects against expensive-but-syntactically-valid queries, like
an accidental cross join).

### A real bug caught during testing

My first version only ran `sqlglot`'s `qualify()` function (which resolves
column references against a schema) and assumed it would also catch
references to tables that don't exist. It doesn't, in one specific case:
`SELECT * FROM wire_transfers` (a hallucinated table) has no explicit
columns to resolve — `qualify()` had nothing to fail on, and the query
sailed through.

Caught this by writing a test table of both valid and adversarial inputs
before wiring the guardrail into the graph (see the "Testing it" section
below) — `wire_transfers` was one of the cases and it passed when it should
have been blocked. Fixed by adding an explicit check: pull every table name
referenced in the query and diff it against the real schema's table names,
*before* even calling `qualify()`. This is a good example of why "looks
right" and "tested against adversarial input" are different bars — I'd
assumed a general-purpose SQL validator covered this case, and it didn't.

## Testing it

Ran a table of nine cases through the guardrail directly (valid selects,
hallucinated columns, hallucinated tables, `DROP`, stacked statements,
disguised `UPDATE`, non-SQL text) — after the fix, every case resolved
correctly: valid queries pass with a `LIMIT` appended, every adversarial
case is blocked with a specific reason.

The retry loop needed a different testing approach — the real model won't
reliably produce broken SQL on command, so `tests/test_graph_repair_loop.py`
mocks the LLM call (`app.graph._call_claude`) with a scripted sequence of
responses instead. This tests the graph's *routing logic* (does it actually
retry, does it actually give up after the limit), independent of the real
model's SQL quality — that part was already validated with a real question
end-to-end (`which counterparties are sanctioned` → correct SQL, correct 7
rows, executed on the first attempt).

```bash
./venv/bin/python -m app.graph "which counterparties are sanctioned"
./venv/bin/python -m tests.test_graph_repair_loop
```

Results: happy path found all 7 sanctioned counterparties correctly in one
attempt; the mocked repair-loop tests confirmed the graph recovers after a
bad attempt (`retry_count == 1`, then succeeds) and gives up cleanly after
`MAX_RETRIES` is exceeded (3 total attempts, no query executed, errors still
visible in the final state) — separately confirmed the timeout guard fires
by forcing a 0-second deadline against a real cross-join query.

## Phase 3 status: done

The chatbot can now safely refuse to run a query it shouldn't, try to fix
its own mistakes within a bound, and give up cleanly (not silently) if it
can't. Next up (Phase 4) is the part that actually decides what's
*suspicious* — plain Python, no LLM involved.
