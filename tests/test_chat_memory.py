"""Checks Phase 6's memory mechanism: does chat_history actually accumulate
across turns on the same thread (via the checkpointer + operator.add
reducer), and does generate_sql_node actually include it in the prompt for
follow-up questions. Mocked LLM calls — no API cost, and no dependency on
the real model reliably producing any particular SQL.

Run with: ./venv/bin/python -m tests.test_chat_memory
"""

import uuid
from unittest.mock import patch

from app.graph import SYNTHESIS_SYSTEM_PROMPT, build_graph, generate_sql_node


def _fake_call_claude(sql_response: str, synthesis_response: str = "mocked answer"):
    def _call(system_prompt, user_message, max_tokens=500):
        if system_prompt == SYNTHESIS_SYSTEM_PROMPT:
            return synthesis_response
        return sql_response
    return _call


def test_chat_history_accumulates_across_turns_on_same_thread():
    app = build_graph(use_checkpointer=True)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    with patch("app.graph._call_claude", side_effect=_fake_call_claude("SELECT * FROM transactions")):
        r1 = app.invoke({"user_query": "first question", "retry_count": 0, "validation_errors": []}, config)
        r2 = app.invoke({"user_query": "second question", "retry_count": 0, "validation_errors": []}, config)

    assert len(r1["chat_history"]) == 1, f"expected 1 turn after first question, got {len(r1['chat_history'])}"
    assert len(r2["chat_history"]) == 2, f"expected 2 turns after second question, got {len(r2['chat_history'])}"
    assert r2["chat_history"][0]["question"] == "first question"
    assert r2["chat_history"][1]["question"] == "second question"
    print("PASS: chat_history accumulates across turns on the same thread_id")


def test_different_threads_do_not_share_history():
    app = build_graph(use_checkpointer=True)
    config_a = {"configurable": {"thread_id": str(uuid.uuid4())}}
    config_b = {"configurable": {"thread_id": str(uuid.uuid4())}}

    with patch("app.graph._call_claude", side_effect=_fake_call_claude("SELECT * FROM transactions")):
        app.invoke({"user_query": "question on thread A", "retry_count": 0, "validation_errors": []}, config_a)
        r_b = app.invoke({"user_query": "question on thread B", "retry_count": 0, "validation_errors": []}, config_b)

    assert len(r_b["chat_history"]) == 1, "thread B should not see thread A's history"
    print("PASS: separate thread_ids keep independent history")


def test_generate_sql_includes_history_only_on_followups():
    no_history_state = {"user_query": "first question", "retrieved_schema": "transactions(...)", "retry_count": 0}
    with_history_state = {
        "user_query": "follow-up question",
        "retrieved_schema": "transactions(...)",
        "retry_count": 0,
        "chat_history": [{"question": "first question", "sql": "SELECT 1", "answer": "some answer"}],
    }
    captured = {}

    def capturing_call(system_prompt, user_message, max_tokens=500):
        captured["user_message"] = user_message
        return "SELECT 1"

    with patch("app.graph._call_claude", side_effect=capturing_call):
        generate_sql_node(no_history_state)
        assert captured["user_message"] == "first question", "first turn should send the bare question, no history framing"

        generate_sql_node(with_history_state)
        assert "first question" in captured["user_message"], "follow-up should include prior question for context"
        assert "follow-up question" in captured["user_message"]

    print("PASS: generate_sql_node only injects conversation history on follow-up turns")


if __name__ == "__main__":
    test_chat_history_accumulates_across_turns_on_same_thread()
    test_different_threads_do_not_share_history()
    test_generate_sql_includes_history_only_on_followups()
