"""The full LangGraph pipeline: NL question -> schema retrieval -> SQL ->
guardrail (with a bounded repair loop) -> execution -> deterministic AML
policy engine -> natural-language answer.

    [retrieve_schema] -> [generate_sql] -> [validate_sql] --fails, retries left--> back to [generate_sql]
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

Run with: ./venv/bin/python -m app.graph "your question here"
"""

import operator
import re
import sqlite3
import sys
import time
from typing import Annotated, Optional, TypedDict

from anthropic import Anthropic
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.guardrail import ROW_LIMIT, QUERY_TIMEOUT_SECONDS, make_timeout_handler, validate_sql
from app.policy_engine import load_transactions_df, run_policy_engine
from app.schema import build_system_prompt
from app.schema_rag import retrieve_schema_text

load_dotenv()

DB_PATH = "db/aml.db"
MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 2

SYNTHESIS_SYSTEM_PROMPT = """You are an AML compliance assistant explaining \
query results to an analyst in plain English.

You'll be given the analyst's original question, the raw query results \
(row count and a sample of rows), and structured findings from a \
deterministic rules engine (if any applied to this data).

Rules:
- Only state facts and numbers that appear in the query results or \
findings below. Never invent a transaction, amount, or finding that isn't \
there.
- If there are findings, summarize them clearly: how many, which rules \
triggered, at what severity, and which specific transactions.
- If there are no findings AND no note below explaining why, say so \
plainly — the rules engine ran and found nothing notable.
- If there ARE no findings AND a note below explains the rules engine did \
not run at all, you MUST relay that honestly: say the automated checks \
could not run on this data (and why, briefly), never that the data was \
"reviewed and found clean," and never speculate about "gaps in the \
ruleset" — the absence of findings here reflects a scoping limitation, not \
a rules-engine conclusion.
- If the question wasn't really about suspicious activity (e.g. "which \
counterparties are sanctioned"), just describe the data directly — don't \
force a findings summary that doesn't apply.
- Be concise. A compliance analyst wants the answer, not a report.
"""


class GraphState(TypedDict, total=False):
    user_query: str
    retrieved_schema: str
    generated_sql: str
    safe_sql: str
    validation_errors: list[str]
    retry_count: int
    columns: list[str]
    rows: list[tuple]
    row_limit: Optional[int]
    error: Optional[str]
    policy_findings: list[dict]
    policy_engine_note: Optional[str]
    final_answer: str
    # Annotated with operator.add so returning {"chat_history": [turn]} from
    # record_history_node *appends* via LangGraph's reducer instead of
    # overwriting — each turn accumulates onto what a checkpointer persisted
    # from prior turns on the same thread_id, rather than erasing it.
    chat_history: Annotated[list[dict], operator.add]


def _call_claude(system_prompt: str, user_message: str, max_tokens: int = 500) -> str:
    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text.strip()


def schema_retrieval_node(state: GraphState) -> dict:
    """Phase 5: retrieves only the tables relevant to this question instead
    of dumping the full schema into every prompt. See app/schema_rag.py."""
    return {"retrieved_schema": retrieve_schema_text(state["user_query"])}


def generate_sql_node(state: GraphState) -> dict:
    system_prompt = build_system_prompt(state["retrieved_schema"])
    if state.get("retry_count", 0) == 0:
        history = state.get("chat_history", [])
        if history:
            # Only the last few turns — enough to resolve "that one" / "what
            # about the sanctioned ones" style follow-ups without letting a
            # long conversation balloon every subsequent prompt.
            history_text = "\n\n".join(
                f"Q: {h['question']}\nSQL used: {h['sql']}\nAnswer given: {h['answer']}"
                for h in history[-3:]
            )
            user_message = (
                f"Recent conversation (for resolving follow-up references "
                f"like \"that one\" or \"those transactions\"):\n{history_text}\n\n"
                f"New question: {state['user_query']}"
            )
        else:
            user_message = state["user_query"]
    else:
        errors = "\n".join(f"- {e}" for e in state["validation_errors"])
        user_message = (
            f"Original question: {state['user_query']}\n\n"
            f"Your previous SQL:\n{state['generated_sql']}\n\n"
            f"That failed validation with these errors:\n{errors}\n\n"
            "Fix the SQL and return only the corrected query."
        )
    raw = _call_claude(system_prompt, user_message)
    sql = re.sub(r"^```sql\s*|```\s*$", "", raw, flags=re.MULTILINE).strip()
    return {"generated_sql": sql}


def validate_sql_node(state: GraphState) -> dict:
    safe_sql, errors, row_limit = validate_sql(state["generated_sql"])
    retry_count = state.get("retry_count", 0)
    if errors:
        retry_count += 1
    return {
        "safe_sql": safe_sql or "",
        "validation_errors": errors,
        "retry_count": retry_count,
        "row_limit": row_limit,
    }


def route_after_validation(state: GraphState) -> str:
    if not state["validation_errors"]:
        return "execute"
    if state["retry_count"] > MAX_RETRIES:
        return "fail"
    return "retry"


def execute_sql_node(state: GraphState) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    conn.set_progress_handler(make_timeout_handler(deadline), 1000)
    try:
        cur = conn.cursor()
        cur.execute(state["safe_sql"])
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
        return {"columns": columns, "rows": [tuple(r) for r in rows], "error": None}
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            return {"error": f"Query exceeded {QUERY_TIMEOUT_SECONDS}s timeout"}
        return {"error": str(e)}
    finally:
        conn.close()


def route_after_execution(state: GraphState) -> str:
    return "error" if state.get("error") else "analyze"


def policy_engine_node(state: GraphState) -> dict:
    """Re-queries the DB for canonical transaction data rather than trusting
    the SQL Executor's raw columns — see the docstring on
    `load_transactions_df` for why. If there's no transaction_id column to
    scope by, the policy engine can't run — this happens not just for
    genuinely non-transaction questions (e.g. "which counterparties are
    sanctioned") but also for *aggregated* transaction queries (e.g.
    `GROUP BY account_id` with `GROUP_CONCAT(transaction_id)`), where the
    per-row transaction_id simply isn't exposed as its own column.

    Either way, `policy_engine_note` records *why* no findings ran, so
    synthesize_response_node never has to guess — and can't accidentally
    imply the rules engine reviewed and cleared data it never actually saw.
    """
    columns = state["columns"]
    if "transaction_id" not in columns:
        return {
            "policy_findings": [],
            "policy_engine_note": (
                "The rules engine did NOT run on this query's results — there was no "
                "transaction_id column to scope it to (either because this wasn't a "
                "transaction-level question, or because the query aggregated/grouped "
                "the data, e.g. with GROUP BY or GROUP_CONCAT). This means zero "
                "findings here says nothing about whether these transactions are "
                "clean — the rules engine simply never evaluated them individually."
            ),
        }

    idx = columns.index("transaction_id")
    transaction_ids = sorted({row[idx] for row in state["rows"]})
    if not transaction_ids:
        return {"policy_findings": [], "policy_engine_note": None}

    df = load_transactions_df(transaction_ids=transaction_ids)
    findings = run_policy_engine(df)
    return {"policy_findings": [f.as_dict() for f in findings], "policy_engine_note": None}


def synthesize_response_node(state: GraphState) -> dict:
    if state.get("error"):
        return {"final_answer": f"I couldn't complete that query: {state['error']}"}

    rows = state["rows"]
    columns = state["columns"]
    findings = state.get("policy_findings", [])
    row_limit = state.get("row_limit")

    sample = "\n".join(str(dict(zip(columns, row))) for row in rows[:15])
    findings_text = "\n".join(
        f"- txn {f['transaction_id']}: {f['rule_triggered']} ({f['severity']}) — {f['explanation']}"
        for f in findings
    ) or "(none)"

    truncation_note = ""
    if row_limit == ROW_LIMIT and len(rows) >= row_limit:
        # Only warn when the effective limit is the guardrail's own
        # injected default (ROW_LIMIT) — not any small LIMIT the model
        # deliberately wrote itself (e.g. "which one had the most" -> its
        # own `LIMIT 1`, which is a correct, intentional result, not
        # truncation). A row count matching the *safety cap* specifically
        # doesn't guarantee more rows exist, but it's the only signal
        # available, and understating completeness is a far safer failure
        # mode than overstating it.
        truncation_note = (
            f"\n\nIMPORTANT: the query hit its {row_limit}-row safety cap. There may be "
            "additional matching transactions not included in this analysis — you must "
            "tell the analyst results may be incomplete, don't imply this is exhaustive."
        )

    policy_engine_note = state.get("policy_engine_note")
    note_text = f"\n\nNote on the rules engine: {policy_engine_note}" if policy_engine_note else ""

    user_message = (
        f"Original question: {state['user_query']}\n\n"
        f"Query returned {len(rows)} rows. Columns: {columns}\n"
        f"Sample rows (up to 15):\n{sample}\n\n"
        f"Policy engine findings ({len(findings)} total):\n{findings_text}"
        f"{note_text}"
        f"{truncation_note}"
    )
    answer = _call_claude(SYNTHESIS_SYSTEM_PROMPT, user_message, max_tokens=2000)
    return {"final_answer": answer}


def record_history_node(state: GraphState) -> dict:
    """Phase 6: appends this turn to chat_history (via the operator.add
    reducer on the field). With a checkpointer + a stable thread_id, this is
    what makes follow-up questions on the same thread able to reference
    what just happened."""
    turn = {
        "question": state["user_query"],
        "sql": state.get("safe_sql") or state.get("generated_sql", ""),
        "answer": state.get("final_answer", ""),
    }
    return {"chat_history": [turn]}


def build_graph(use_checkpointer: bool = False):
    graph = StateGraph(GraphState)
    graph.add_node("retrieve_schema", schema_retrieval_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("validate_sql", validate_sql_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("policy_engine", policy_engine_node)
    graph.add_node("synthesize_response", synthesize_response_node)
    graph.add_node("record_history", record_history_node)

    graph.set_entry_point("retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    graph.add_edge("generate_sql", "validate_sql")
    graph.add_conditional_edges(
        "validate_sql",
        route_after_validation,
        {"execute": "execute_sql", "retry": "generate_sql", "fail": END},
    )
    graph.add_conditional_edges(
        "execute_sql",
        route_after_execution,
        {"analyze": "policy_engine", "error": "synthesize_response"},
    )
    graph.add_edge("policy_engine", "synthesize_response")
    graph.add_edge("synthesize_response", "record_history")
    graph.add_edge("record_history", END)

    if use_checkpointer:
        return graph.compile(checkpointer=MemorySaver())
    return graph.compile()


def main():
    question = " ".join(sys.argv[1:]) or "Show me last month's transactions and tell me which are suspicious"
    print(f"Question: {question}\n")

    app = build_graph()
    result = app.invoke({"user_query": question, "retry_count": 0, "validation_errors": []})

    if result.get("validation_errors") and not result.get("safe_sql") and not result.get("final_answer"):
        print(f"FAILED after {MAX_RETRIES} retries. Last errors:")
        for e in result["validation_errors"]:
            print(f"  - {e}")
        return

    retrieved_tables = re.findall(r"^(\w+)\(", result.get("retrieved_schema", ""), re.MULTILINE)
    print(f"Retrieved tables: {retrieved_tables}")
    print(f"SQL: {result.get('safe_sql') or result.get('generated_sql')}")
    print(f"Rows returned: {len(result.get('rows', []))}")
    print(f"Policy findings: {len(result.get('policy_findings', []))}\n")
    print("Answer:")
    print(result["final_answer"])


if __name__ == "__main__":
    main()
