"""Proves the guardrail repair loop actually works: the graph should retry
on bad SQL and recover, or give up cleanly after MAX_RETRIES. The real LLM
can't be reliably forced to produce broken SQL on demand, so the LLM call
is mocked with a scripted sequence of responses instead — this tests the
graph's *routing logic*, not the model's SQL quality (that's what the
guardrail unit tests and the real Phase 3 run already covered).

Run with: ./venv/bin/python -m tests.test_graph_repair_loop
"""

from unittest.mock import patch

from app.graph import MAX_RETRIES, SYNTHESIS_SYSTEM_PROMPT, build_graph


def _mock_call_claude(sql_responses):
    """_call_claude is now shared by SQL generation and the Response
    Synthesizer, which is also a node this graph reaches on the happy
    path — route on which system prompt is being used so a SQL-generation
    mock doesn't get consumed by (or collide with) the synthesis call."""
    sql_iter = iter(sql_responses)

    def _call(system_prompt, user_message, max_tokens=500):
        if system_prompt == SYNTHESIS_SYSTEM_PROMPT:
            return "Mocked synthesis answer (not a real LLM call in this test)."
        return next(sql_iter)

    return _call


def test_recovers_after_one_bad_attempt():
    responses = ["SELECT * FROM nonexistent_table", "SELECT * FROM transactions LIMIT 5"]
    with patch("app.graph._call_claude", side_effect=_mock_call_claude(responses)):
        app = build_graph()
        result = app.invoke({"user_query": "test", "retry_count": 0, "validation_errors": []})

    assert result["retry_count"] == 1, f"expected 1 retry, got {result['retry_count']}"
    assert result["safe_sql"], "expected a validated query after recovery"
    assert result["rows"], "expected rows back from the recovered query"
    print("PASS: recovers after one bad attempt (retry_count=1, query executed)")


def test_gives_up_after_max_retries():
    always_bad = "SELECT * FROM nonexistent_table"
    with patch("app.graph._call_claude", side_effect=lambda *_: always_bad):
        app = build_graph()
        result = app.invoke({"user_query": "test", "retry_count": 0, "validation_errors": []})

    expected_attempts = MAX_RETRIES + 1
    assert result["retry_count"] == expected_attempts, (
        f"expected {expected_attempts} attempts, got {result['retry_count']}"
    )
    assert not result["safe_sql"], "should not have produced a safe query"
    assert result["validation_errors"], "should still have validation errors set"
    print(f"PASS: gives up cleanly after {expected_attempts} attempts, no query executed")


if __name__ == "__main__":
    test_recovers_after_one_bad_attempt()
    test_gives_up_after_max_retries()
