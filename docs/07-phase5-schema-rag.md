# Phase 5 — Schema Retrieval (RAG)

## Files created/changed

- [`app/schema.py`](../app/schema.py) — `SCHEMA_DESCRIPTION` split into per-table `TABLE_DESCRIPTIONS`; added `build_system_prompt()`
- [`app/schema_rag.py`](../app/schema_rag.py) — the retrieval index
- [`app/graph.py`](../app/graph.py) — new `retrieve_schema` node, now the graph's entry point
- [`tests/test_schema_rag.py`](../tests/test_schema_rag.py) — regression guard

## What "RAG" means here, concretely

Up through Phase 4, every single prompt sent the *entire* schema — all 8
tables — regardless of the question. That's fine at 8 tables, but the
architecture doc calls out that this stops being fine past 5-6 tables (a
real bank schema might have hundreds). Phase 5 replaces "always send
everything" with: **embed each table's description once, then per-question,
retrieve only the top-k most relevant ones and put only those in the
prompt.**

Concretely:

1. `app/schema.py` now defines `TABLE_DESCRIPTIONS`, a dict of `{table_name:
   description}` instead of one giant string.
2. `app/schema_rag.py` embeds each description into a persistent Chroma
   vector index (`./chroma_db`, gitignored) using Chroma's **default local
   embedding model** (`all-MiniLM-L6-v2`, runs on-device via ONNX) — no
   embedding API key needed, so this stayed self-contained rather than
   requiring another provider decision.
3. A new `retrieve_schema` node runs *first* in the graph (before SQL
   generation), embeds the user's question, and retrieves the 5
   closest-matching table descriptions.
4. `generate_sql_node` now builds its system prompt fresh each call via
   `build_system_prompt(state["retrieved_schema"])`, instead of importing
   one static prompt built from the full schema.

## A retrieval-quality bug, caught before wiring it in

First test of retrieval (before touching the graph) ran the flagship
question — *"show me last month's transactions and tell me which are
suspicious"* — through the retriever alone. At `k=5`, and even at `k=6` (of
only 8 tables total), **`counterparties` never made the cut.**

That's a real problem, not a cosmetic one: `counterparties` is where
`is_sanctioned`, `is_pep`, and `country` live — exactly the fields needed to
judge suspicion. The retriever kept preferring `sar_filings` and `alerts`
instead, because their descriptions literally contain the word "Suspicious"
("Suspicious Activity Reports") while `counterparties`'s description didn't
share that surface vocabulary with the question — a known weak spot of
embedding similarity on short, keyword-sparse text: it can favor lexical
overlap over the semantically correct match.

*(Caught before this ever reached the graph — tested the retriever in
isolation first with `./venv/bin/python -m app.schema_rag "..."` and a set
of representative questions, the same pattern used for the guardrail and
policy engine in earlier phases: verify the risky new component alone
before wiring it into something bigger.)*

Fixed by enriching the `transactions` and `counterparties` descriptions with
representative question phrasings and explicit keywords ("suspicious,"
"risky counterparty," "sanctions screening") — a standard RAG technique
(document expansion) rather than a hack. Re-tested: `counterparties` now
reliably appears in the top 5 for the flagship question and every other
representative question tried.

## Testing it

`tests/test_schema_rag.py` locks in the fix as a regression guard — three
checks that specific critical tables must appear in the top-5 retrieval for
specific question types, so a future change to the descriptions can't
silently reintroduce the bug.

Real end-to-end confirmation: ran the flagship question through the full
graph with retrieval now wired in (`retrieve_schema → generate_sql → ...`).
Retrieved `['sar_filings', 'transactions', 'alerts', 'counterparties',
'accounts']` (5 of 8 tables) and produced the exact same quality of answer
as the full-schema version from the previous phase — 551 rows, 113
findings, correctly structured — confirming retrieval didn't cost any
accuracy.

## Honest limitation: the benefit here is architectural, not dramatic

At 8 tables, retrieving 5 of them doesn't save much prompt space — this is
true to what the architecture doc itself says ("this matters more as your
synthetic DB grows past 5-6 tables"). The value of building this phase
properly is that the *mechanism* is now in place and tested — at a schema
with 50 or 200 tables, the same `retrieve_schema` node would provide a real,
necessary token/cost saving, without any other part of the graph needing to
change. The guardrail's table/column allowlist (Phase 3) still checks
against the *full* real schema regardless of what got retrieved — retrieval
only affects what goes in the prompt, never what's allowed to execute.

## Phase 5 status: done
