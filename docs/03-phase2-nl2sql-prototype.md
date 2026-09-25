# Phase 2 — Single-Shot NL→SQL Prototype

## Files created

- [`app/schema.py`](../app/schema.py) — hardcoded schema description + AML-vocabulary few-shot examples, fed into the prompt
- [`app/nl2sql_prototype.py`](../app/nl2sql_prototype.py) — the actual loop: question → SQL → results

## What this proves (and deliberately doesn't attempt yet)

This is intentionally the simplest possible version: one Python script, one
LLM call, no LangGraph, no guardrail/validation layer, no policy engine. Per
the build order, the point is to prove the *core idea* works before adding
structure around it — if NL→SQL itself doesn't work, no amount of graph
architecture fixes that.

### How it works

1. `SYSTEM_PROMPT` (built in `nl2sql_prototype.py`) bundles: the full schema
   description, a few worked examples of AML-specific phrasing → SQL
   mappings, and hard rules (SELECT-only, no markdown fences, use SQLite
   date functions instead of hardcoded dates).
2. `generate_sql()` sends the user's question to Claude
   (`claude-sonnet-4-6`) and strips any markdown fencing from the reply.
3. `run_query()` executes the returned SQL directly against `db/aml.db`
   with `sqlite3` and returns rows.

No guardrails yet — this happily executes whatever SQL Claude writes. That's
fine for Phase 2 because we're only running it ourselves, on a read-only
question set, against synthetic data. Phase 3 adds the safety layer before
this becomes something exposed to a chat interface.

### The most important design decision, tested for real

The system prompt explicitly instructs the model:

> Do not attempt to judge which transactions are "suspicious" yourself —
> that determination belongs to a separate rules engine, not to SQL
> generation.

I tested this with the exact kind of question from the original ask —
*"show me last month's transactions and tell me which are suspicious"* — and
the model did the right thing: it did **not** try to invent a definition of
"suspicious" in SQL. It generated a query that joins `transactions` with
`counterparties`, `customers`, and `accounts` and pulls back exactly the
fields a rules engine would need to judge suspicion (`is_sanctioned`,
`is_pep`, `risk_rating`) — then stopped. It returned data, not a verdict.
That's the architecture's core separation of concerns working as intended,
confirmed with a real model call rather than just a design assumption.

## Test results

```bash
./venv/bin/python -m app.nl2sql_prototype "Show me last month's transactions over 10000 dollars"
./venv/bin/python -m app.nl2sql_prototype "show me last month's transactions and tell me which are suspicious"
./venv/bin/python -m app.nl2sql_prototype "which accounts show signs of structuring or smurfing"
```

The structuring question is the best proof point: it generated

```sql
SELECT account_id, COUNT(*) AS n_transactions, SUM(amount) AS total_amount,
       GROUP_CONCAT(transaction_id) AS txn_ids
FROM transactions
WHERE amount BETWEEN 9000 AND 9999.99
GROUP BY account_id
HAVING COUNT(*) >= 3
```

and found **all 8** of the structuring sequences we deliberately planted in
Phase 1 (accounts 21, 88, 96, 118, 158, 172, 187, 208) — matching exactly
the ground truth from `tests/verify_seed.py`.

Note: "last month" now resolves relative to the actual current date, which
has moved on since the database was seeded — so a query for "last month"
today returns different (correct, just smaller) results than it would have
right after seeding. This is expected: `txn_date` values are fixed at seed
time, but "what counts as last month" is evaluated fresh every time a query
runs.

## Known limitations (by design, addressed in later phases)

- **No safety guardrails yet** — nothing stops a hallucinated table/column
  reference, an expensive query, or (in principle) a non-SELECT statement
  beyond the prompt's instruction, which an LLM can ignore. → Phase 3.
- **No deterministic policy logic** — "suspicious" isn't actually evaluated
  anywhere yet, by design. → Phase 4.
- **Whole schema in every prompt** — fine at 8 tables, wouldn't scale to a
  real bank's schema. → Phase 5.
- **No memory** — every question is independent; "tell me more about that
  one" wouldn't work. → Phase 6.

## Phase 2 status: done
