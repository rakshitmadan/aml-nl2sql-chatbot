# Overview — What We're Building and Why

## The goal, in plain English

You want to type something like *"show me last month's transactions and tell me
which ones are suspicious"* into a chat box, and get back a written answer like:
*"You had 20 transactions last month; 1 exceeded the $10,000 CTR threshold..."*

Under the hood, three very different jobs have to happen for that to work:

1. **Turn English into SQL** — an LLM's job. LLMs are good at language, bad at
   being trusted with exact numbers.
2. **Decide what counts as "suspicious"** — a compliance/rules job. This must be
   deterministic (same input always gives same output), auditable, and never
   invented by a language model. A bank regulator will ask "why was this
   flagged?" and "it seemed suspicious to the AI" is not an acceptable answer.
3. **Turn the rule results back into English** — an LLM's job again, but this
   time it's *only allowed* to describe numbers that already exist — it must
   not calculate or invent them.

This is why the architecture is a **pipeline of separate steps (a "graph")**
instead of one big prompt. Each step does one job, can be tested on its own,
and mistakes in one step don't quietly corrupt the others.

## Why LangGraph specifically

LangGraph lets you describe that pipeline as a state machine: a set of "nodes"
(steps) connected by edges (what runs next), sharing one growing bundle of
data called **state** (the running record of the conversation — user's
question, the SQL generated, the rows returned, the rule findings, etc.).
Compared to just calling an LLM in a loop yourself, LangGraph gives you:

- **Conditional routing** — e.g. "if SQL validation fails, go back and try
  again, but only twice" — without hand-rolled control flow.
- **Checkpointing** — the state can be saved after each turn, so a follow-up
  question like "tell me more about that one" can see what happened earlier
  in the conversation.
- **Swappable pieces** — each node just calls *something* (an LLM, a plain
  Python function). You can change the LLM provider, or replace a node
  entirely, without touching the rest of the graph.

## The build order we're following

We are **not** building the whole graph on day one. We build it in layers,
proving each one works before adding the next — this mirrors how you'd
actually build (and explain, in an interview) a system like this:

| Phase | What we build | Why this order |
|---|---|---|
| 1 | Synthetic AML database (schema + seeded fake data) | Nothing else works without data to query |
| 2 | A single hardcoded "ask a question, get SQL back" script | Prove the core NL→SQL idea works before adding orchestration complexity |
| 3 | Wrap it in a LangGraph graph with a safety-check node | Add structure and guardrails around the working core |
| 4 | The AML rule engine (plain Python, no LLM) | The compliance logic — the part that actually matters |
| 5 | Schema search (RAG) | Only needed once the database has enough tables that dumping the whole schema into every prompt gets expensive |
| 6 | Conversation memory | Multi-turn follow-ups |
| 7 | A chat UI (Streamlit) | So you can actually demo it |

We're starting at **Phase 1** now: the database. Everything downstream reads
from it, so it needs to exist first, and it needs to contain *realistic,
intentional* suspicious patterns — not just random noise — or there will be
nothing for the chatbot to correctly find later.

## Project layout

```
aml-nl2sql-chatbot/
├── venv/               # isolated Python environment (not committed to git)
├── requirements.txt    # Python packages this project depends on, grouped by phase
├── docs/                # explanations like this one — one per phase
├── db/
│   ├── models.py         # table definitions (Phase 1)
│   └── seed.py             # fake-data generator (Phase 1)
├── app/                     # the LangGraph app will live here (Phase 2+)
└── tests/                     # automated checks
```
