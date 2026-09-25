# Integration — Wiring the Policy Engine + Response Synthesizer into the Graph

This is the point where the chatbot became able to actually answer the
question from the original ask end-to-end: *"show me last month's
transactions and tell me which of them are suspicious."*

## Files changed

- [`app/graph.py`](../app/graph.py) — two new nodes, extended `GraphState`
- [`app/policy_engine.py`](../app/policy_engine.py) — `load_transactions_df` gained a `transaction_ids` filter
- [`app/guardrail.py`](../app/guardrail.py) — `validate_sql` now also returns the effective row limit
- [`tests/test_graph_integration.py`](../tests/test_graph_integration.py) — new

## The graph now, in full

```
[generate_sql] -> [validate_sql] --fails, retries left--> back to [generate_sql]
                        |
                   passes  |  exhausted retries
                        v  v
                 [execute_sql]   END (validation_errors still set)
                        |
                error?  |  ok
                   v    v
          [synthesize_response] <- [policy_engine]
                        |
                       END
```

## Design decision: the policy engine doesn't trust the executor's columns

The obvious way to wire this would be: take whatever columns/rows
`execute_sql` returned, hand them straight to `run_policy_engine()`. That
doesn't work — the NL→SQL generator picks its own column aliases per
question. One test earlier aliased sanctions/PEP flags as `is_sanctioned`,
another as `counterparty_is_sanctioned`. The policy engine needs a fixed,
predictable column contract; an LLM's phrasing-dependent `SELECT` list
can't reliably provide that.

Fix: `policy_engine_node` only reads one thing from the executor's output —
whether a `transaction_id` column exists at all (if not, this wasn't a
transaction-shaped query, and there's nothing to analyze). If it does exist,
it pulls out the set of transaction IDs and re-queries the database itself,
using `load_transactions_df(transaction_ids=...)`, which always returns the
exact columns the policy engine requires. The LLM's original query still
determined *which* transactions were in scope; it just doesn't get to
determine the shape of the data the rules run against.

## Bug found while testing: the row-limit guardrail was silently hiding data from the policy engine

Running the full pipeline on the "last month...suspicious" question the
first time returned exactly 500 rows — suspiciously round. Checked the real
count directly against the database: **551** actual transactions matched
that date range. The Phase 3 guardrail's `LIMIT 500` (added purely as a
safety cap against runaway queries) had truncated the result *before the
policy engine ever saw the other 51* — and because the generated SQL had no
`ORDER BY`, which 51 rows got cut was essentially arbitrary. A structuring
cluster split across the cutoff could have silently dropped below the
3-transaction threshold and gone undetected entirely — the worst possible
failure mode for a compliance tool: not "flagged the wrong thing," but
"silently missed something real."

Two-part fix:

1. **Raised `ROW_LIMIT` to 5,000** (`app/guardrail.py`) — large enough that
   it won't truncate realistic query volumes against this project's ~2,900-row
   database, while still guarding against a truly unbounded query (an
   unfiltered cross join, for instance). The row limit's real remaining job
   is a coarse sanity cap, not correctness — actual runaway-query protection
   is `execute_sql_node`'s wall-clock timeout guard.
2. **Truncation is now detected and disclosed rather than silently
   swallowed.** `validate_sql` returns the effective limit it applied;
   `synthesize_response_node` compares that against the actual row count
   and, if they're equal, adds an explicit instruction to the LLM prompt:
   *tell the analyst results may be incomplete, don't imply this is
   exhaustive.* Understating completeness is a far safer failure mode than
   overstating it — an analyst told "there might be more" can go check one
   told "this is everything" has no reason to.

After the fix, the same question correctly returned and analyzed all 551
transactions, with **113 policy findings** across all 5 rules (structuring
across exactly the 8 known accounts, 5 sanctions matches, the known velocity
spike, etc.) — matching Phase 1/4 ground truth exactly.

## Testing it

`tests/test_graph_integration.py` covers the two new nodes directly (no API
calls needed, mocked the same way as the Phase 3 repair-loop tests):

- `policy_engine_node` correctly no-ops on a non-transaction result (e.g.
  "which counterparties are sanctioned") instead of erroring
- `policy_engine_node` correctly scopes analysis to exactly the
  transaction_ids the query returned
- `synthesize_response_node` includes the truncation warning when
  `len(rows) == row_limit`, and correctly omits it otherwise

Real end-to-end runs (`./venv/bin/python -m app.graph "..."`):

- *"show me last month's transactions and tell me which are suspicious"* →
  551 rows, 113 findings, a complete severity-organized natural-language
  breakdown citing real transaction IDs, correctly prioritized (sanctions
  and structuring flagged as needing immediate action, jurisdiction flags
  called out as low-severity/routine)
- *"which counterparties are sanctioned"* → 7 rows, 0 policy findings
  (correctly recognized this wasn't a transaction question), a direct
  description of the data with no forced suspicion narrative

## Status: the chatbot can now answer end-to-end

What's still open: no schema RAG (fine at this table count — Phase 5), no
multi-turn memory (Phase 6), no UI (Phase 7, currently CLI-only via
`app.graph`).
