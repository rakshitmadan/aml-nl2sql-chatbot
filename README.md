# AML NL→SQL Chatbot

A conversational chatbot that lets you ask natural-language questions about
anti-money-laundering (AML) data — e.g. *"show me last month's transactions
and tell me which ones are suspicious"* — and get back a plain-English
answer backed by real SQL queries and deterministic compliance rules (not an
LLM guessing at what's suspicious).

Built as a [LangGraph](https://github.com/langchain-ai/langgraph) state
machine on a synthetic SQLite AML database, using Anthropic's Claude.

## Status: all 7 phases complete

See [docs/STATUS.md](docs/STATUS.md) for the full progress log — what's
built, every bug found and fixed along the way, and how each phase was
verified.

## Docs (read in order)

1. [docs/00-overview.md](docs/00-overview.md) — why this architecture, why LangGraph, the build order
2. [docs/01-database-schema.md](docs/01-database-schema.md) — the 8-table AML schema, SQLAlchemy basics
3. [docs/02-seed-data-and-verification.md](docs/02-seed-data-and-verification.md) — the synthetic data generator and how it was verified
4. [docs/03-phase2-nl2sql-prototype.md](docs/03-phase2-nl2sql-prototype.md) — first working NL→SQL loop
5. [docs/04-phase3-langgraph-guardrail.md](docs/04-phase3-langgraph-guardrail.md) — the LangGraph state machine + SQL safety guardrail
6. [docs/05-phase4-policy-engine.md](docs/05-phase4-policy-engine.md) — the deterministic AML rules engine
7. [docs/06-integration-policy-engine-synthesizer.md](docs/06-integration-policy-engine-synthesizer.md) — wiring it all into one working chatbot
8. [docs/07-phase5-schema-rag.md](docs/07-phase5-schema-rag.md) — schema retrieval (RAG)
9. [docs/08-phase6-conversation-memory.md](docs/08-phase6-conversation-memory.md) — multi-turn conversation memory
10. [docs/09-phase7-streamlit-ui.md](docs/09-phase7-streamlit-ui.md) — the chat UI
11. [docs/STATUS.md](docs/STATUS.md) — full progress log, always current

## Quickstart

```bash
cd aml-nl2sql-chatbot

# (re)build and seed the database — safe to re-run, it's deterministic
rm -f db/aml.db
./venv/bin/python -m db.seed

# verify the planted AML patterns are actually queryable
./venv/bin/python -m tests.verify_seed

# ask a one-off question via CLI
./venv/bin/python -m app.graph "which accounts show signs of structuring or smurfing"

# or start an interactive multi-turn session
./venv/bin/python -m app.chat

# or launch the browser UI
PYTHONPATH=. ./venv/bin/streamlit run app/streamlit_app.py
```

Requires `ANTHROPIC_API_KEY` set in a local `.env` file (gitignored).

### Run the test suite

```bash
for t in tests.verify_seed tests.test_policy_engine tests.test_graph_repair_loop \
         tests.test_graph_integration tests.test_schema_rag tests.test_chat_memory; do
  ./venv/bin/python -m $t
done
```

## What the database contains

`db/aml.db` — synthetic but structurally realistic: customers, accounts,
counterparties, transactions, historical alerts, and SAR filings, with
intentionally planted structuring sequences, CTR threshold breaches,
sanctioned-counterparty hits, and a velocity spike, so there are real
findings for the chatbot to surface. Full detail in
[docs/02-seed-data-and-verification.md](docs/02-seed-data-and-verification.md).

## Architecture

```
[retrieve_schema] -> [generate_sql] -> [validate_sql] --fails, retries--> back to [generate_sql]
                                             |
                                        passes  |  exhausted retries
                                             v  v
                                      [execute_sql]   END
                                             |
                                     error?  |  ok
                                        v    v
                               [synthesize_response] <- [policy_engine]
                                             |
                                    [record_history] -> END (checkpointed)
```

Each node is a plain Python function operating on a shared `TypedDict`
state. The AML Policy Engine (`app/policy_engine.py`) is deliberately the
only part of the pipeline with zero LLM calls — every finding it produces
is reproducible and auditable, the way real compliance logic has to be.
