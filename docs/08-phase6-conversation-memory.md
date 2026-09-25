# Phase 6 — Conversation Memory / Checkpointing

## Files created/changed

- [`app/graph.py`](../app/graph.py) — `chat_history` field, `record_history_node`, checkpointer support
- [`app/chat.py`](../app/chat.py) — interactive multi-turn CLI (new)
- [`app/schema.py`](../app/schema.py) — one new prompt rule (see Bug 5 below)
- [`tests/test_chat_memory.py`](../tests/test_chat_memory.py) — new
- [`tests/test_graph_integration.py`](../tests/test_graph_integration.py) — updated for Bug 6 (below)

## How LangGraph checkpointing actually works

A **checkpointer** persists a graph's state after every step, keyed by a
`thread_id`. Calling `app.invoke(new_input, config={"configurable":
{"thread_id": "abc"}})` again with the *same* thread_id doesn't start from
scratch — LangGraph loads whatever state was last saved for `"abc"` and
merges `new_input` into it before running. That's the entire mechanism that
makes multi-turn conversation possible: it's not the LLM "remembering"
anything, it's the graph's own state being reloaded.

Two pieces were needed to actually use this:

1. **`chat_history` needs to *accumulate*, not overwrite.** Every other
   `GraphState` field uses the default reducer (a node's return value
   replaces the old one) — correct for `rows`, `columns`, etc., which should
   always reflect only the current turn. `chat_history` needs the opposite
   behavior. Declared as `Annotated[list[dict], operator.add]`, so
   returning `{"chat_history": [new_turn]}` from a node *appends* the new
   turn onto whatever the checkpointer already had, rather than replacing
   it.
2. **A node has to actually write to it.** `record_history_node` runs at
   the very end of the graph (after `synthesize_response`) and appends
   `{question, sql, answer}` for the turn that just completed.

`generate_sql_node` was extended to check `state.get("chat_history", [])`
on the *first* attempt at a new question (not on repair-loop retries, which
already have their own different context) — if there's history, the last 3
turns get folded into the prompt so the model can resolve references like
"that one" or "those accounts."

`build_graph(use_checkpointer=True)` compiles with `MemorySaver()` (an
in-memory checkpointer — fine for a single session; a real deployment would
swap in a Postgres-backed one, same idea, just persisted). The original
single-shot `main()` in `app/graph.py` still works unchanged
(`use_checkpointer` defaults to `False`); [`app/chat.py`](../app/chat.py) is
a new interactive REPL that generates one `thread_id` per session and
reuses it across every question typed into that session.

## Two more bugs, both caught by actually running a real multi-turn conversation

Wiring up the mechanism isn't the same as it working correctly — the first
real two-turn conversation I ran surfaced two separate, unrelated bugs:

**Bug 5 — the LLM's own SQL undercounted structuring clusters.** Asked
*"which accounts show signs of structuring or smurfing"*, expecting all 8
known accounts (Phase 1/4 ground truth). Got only 3. The generated SQL was
grouping by `DATE(t.txn_date), t.account_id` instead of just `t.account_id`
— i.e. approximating "clustered within a time window" as "on the same
calendar day." That's wrong for the same reason I already found and fixed
once, in Phase 1's own naive velocity check: a real cluster spanning, say,
11pm to 3am crosses midnight and gets split across two "days," silently
dropping below the 3-transaction threshold on each side. This time the
mistake was the *model's*, not code I wrote — fixed by adding an explicit
rule to the system prompt (`app/schema.py`) warning against exactly this
approximation, and telling the model to return ungrouped candidates instead
if SQL can't express a true rolling window. Re-tested: SQL correctly
dropped the day-grouping, all 8 accounts found, matching ground truth
exactly.

**Bug 6 — the truncation warning (from the Phase 4/integration work)
false-positived on a deliberate small `LIMIT`.** The follow-up question
*"which of those accounts had the most transactions?"* naturally produced
`... ORDER BY n_txns DESC LIMIT 1` — a correct, intentional "top 1" result.
But `synthesize_response_node`'s truncation check was `len(rows) >=
row_limit` for *any* `row_limit`, so `1 >= 1` triggered the same "results
may be incomplete" warning meant for the guardrail's safety cap. Fixed by
comparing specifically against `guardrail.ROW_LIMIT` (the guardrail's own
injected default) instead of any limit value — a model-authored `LIMIT 1`
answering "which one" is a correct result, not truncation.

Both were caught only because Phase 6 forced an actual two-turn
conversation to be tested, not just single questions in isolation — same
pattern as Phase 4's data bug and Phase 5's retrieval bug: integration
reveals what isolated testing can't.

## Testing it

`tests/test_chat_memory.py` (all mocked, no API cost):
- `chat_history` correctly accumulates across two `invoke()` calls on the
  same `thread_id`
- two different `thread_id`s stay completely independent
- `generate_sql_node` only injects history framing on follow-up turns, not
  the first question of a conversation

`tests/test_graph_integration.py` gained a regression test for Bug 6
specifically (a `LIMIT 1` state must never trigger the truncation warning).

Real end-to-end two-turn conversation
(`./venv/bin/python -m app.chat`, or the equivalent scripted `invoke()`
calls):

```
> which accounts show signs of structuring or smurfing
[8 accounts found, matching ground truth exactly: 34, 49, 83, 103, 105, 111, 148, 175]

> which of those accounts had the most transactions in its cluster?
Account 175 had the most transactions in its cluster, with 4 transactions
totalling $38,248.56... [no false truncation warning]
```

The second answer correctly resolved "those accounts" to the specific
8 account IDs from the first turn's SQL — never re-asked, never guessed.

## Phase 6 status: done
