"""Phase 6: interactive multi-turn CLI, backed by LangGraph's in-memory
checkpointer. Each question in the same session shares one thread_id, so
the graph's chat_history accumulates and follow-up questions can reference
earlier ones.

Run with: ./venv/bin/python -m app.chat
"""

import re
import uuid

from app.graph import build_graph


def main():
    app = build_graph(use_checkpointer=True)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print("AML chatbot — multi-turn session. Type 'exit' to quit.\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question or question.lower() in {"exit", "quit"}:
            break

        result = app.invoke({"user_query": question, "retry_count": 0, "validation_errors": []}, config)

        if result.get("validation_errors") and not result.get("safe_sql") and not result.get("final_answer"):
            print("Sorry, I couldn't generate a valid query for that. Errors:")
            for e in result["validation_errors"]:
                print(f"  - {e}")
            print()
            continue

        retrieved_tables = re.findall(r"^(\w+)\(", result.get("retrieved_schema", ""), re.MULTILINE)
        print(f"[tables: {retrieved_tables} | rows: {len(result.get('rows', []))} | findings: {len(result.get('policy_findings', []))}]")
        print(result["final_answer"])
        print()


if __name__ == "__main__":
    main()
