"""Checks the policy-engine and response-synthesis nodes added when wiring
the full pipeline together: does the policy engine correctly no-op on
non-transaction queries, and does the synthesizer correctly warn when the
guardrail's row-limit cap likely truncated results (the Bug 3 scenario: 551
real last-month transactions, 500-row cap, structuring cluster silently
split) — while NOT warning when a small LIMIT was the model's own
deliberate choice (the Bug 4 scenario: a "top 1" question got flagged as
truncated just because len(rows) == row_limit == 1).

Run with: ./venv/bin/python -m tests.test_graph_integration
"""

from unittest.mock import patch

from app.graph import ROW_LIMIT, policy_engine_node, synthesize_response_node


def test_policy_engine_skips_non_transaction_results():
    state = {"columns": ["counterparty_id", "name", "is_sanctioned"], "rows": [(1, "Acme", 1)]}
    result = policy_engine_node(state)
    assert result["policy_findings"] == []
    assert result["policy_engine_note"], "expected a note explaining why nothing ran"
    print("PASS: policy_engine_node no-ops when there's no transaction_id column, and explains why")


def test_policy_engine_note_absent_when_it_actually_ran():
    # 2839 is a known sanctioned-counterparty transaction (see tests/test_policy_engine.py)
    state = {"columns": ["transaction_id", "amount"], "rows": [(2839, 21377.83)]}
    result = policy_engine_node(state)
    assert result["policy_engine_note"] is None, "should not attach a note when the engine actually ran"
    print("PASS: policy_engine_node leaves no note when it actually ran")


def test_policy_engine_scopes_to_returned_transaction_ids():
    # 2839 is a known sanctioned-counterparty transaction (see tests/test_policy_engine.py)
    state = {"columns": ["transaction_id", "amount"], "rows": [(2839, 21377.83)]}
    result = policy_engine_node(state)
    rules = {f["rule_triggered"] for f in result["policy_findings"]}
    assert "sanctions_match" in rules, f"expected sanctions_match, got {rules}"
    print("PASS: policy_engine_node correctly analyzes only the scoped transaction_ids")


def _run_synthesizer_capturing_prompt(state):
    captured = {}

    def fake_call_claude(system_prompt, user_message, max_tokens=500):
        captured["user_message"] = user_message
        return "mocked answer"

    with patch("app.graph._call_claude", side_effect=fake_call_claude):
        synthesize_response_node(state)
    return captured["user_message"]


def test_synthesizer_warns_when_hitting_the_guardrail_default_cap():
    state = {
        "user_query": "test",
        "columns": ["transaction_id"],
        "rows": [(i,) for i in range(ROW_LIMIT)],
        "row_limit": ROW_LIMIT,
        "policy_findings": [],
    }
    prompt = _run_synthesizer_capturing_prompt(state)
    assert "IMPORTANT" in prompt, "expected a truncation warning when rows == the guardrail's default cap"
    print("PASS: synthesizer warns when rows hit the guardrail's own default cap")


def test_synthesizer_no_warning_when_under_limit():
    state = {
        "user_query": "test",
        "columns": ["transaction_id"],
        "rows": [(1,), (2,)],
        "row_limit": ROW_LIMIT,
        "policy_findings": [],
    }
    prompt = _run_synthesizer_capturing_prompt(state)
    assert "IMPORTANT" not in prompt, "should not warn when well under the row limit"
    print("PASS: synthesizer stays silent about truncation when results are clearly complete")


def test_synthesizer_relays_policy_engine_note_when_present():
    # Regression guard for Bug 7: a synthesizer that doesn't know *why*
    # findings are empty can invent an explanation (observed in the real
    # Streamlit UI: it claimed "the policy engine did not flag these...gap
    # in the ruleset" for a GROUP BY query the engine never even saw,
    # because there was no raw transaction_id to scope it to).
    state = {
        "user_query": "which accounts show signs of structuring",
        "columns": ["account_id", "n_txns"],
        "rows": [(34, 4)],
        "row_limit": ROW_LIMIT,
        "policy_findings": [],
        "policy_engine_note": "The rules engine did NOT run on this query's results — no transaction_id column.",
    }
    prompt = _run_synthesizer_capturing_prompt(state)
    assert "did NOT run" in prompt, "the policy engine's explanation must reach the synthesis prompt verbatim"
    print("PASS: synthesizer receives the policy engine's note explaining why findings are empty")


def test_synthesizer_no_warning_for_a_deliberate_small_limit():
    # Regression guard: a "which one had the most" style question can
    # legitimately produce its own small LIMIT (e.g. LIMIT 1) — that's a
    # correct, intentional top-N result, not truncation, even though
    # len(rows) == row_limit here too.
    state = {
        "user_query": "which account had the most transactions?",
        "columns": ["transaction_id"],
        "rows": [(1,)],
        "row_limit": 1,
        "policy_findings": [],
    }
    prompt = _run_synthesizer_capturing_prompt(state)
    assert "IMPORTANT" not in prompt, "a model-authored LIMIT 1 should not be flagged as truncation"
    print("PASS: synthesizer does not misflag a deliberate small LIMIT as truncation")


if __name__ == "__main__":
    test_policy_engine_skips_non_transaction_results()
    test_policy_engine_note_absent_when_it_actually_ran()
    test_policy_engine_scopes_to_returned_transaction_ids()
    test_synthesizer_warns_when_hitting_the_guardrail_default_cap()
    test_synthesizer_no_warning_when_under_limit()
    test_synthesizer_relays_policy_engine_note_when_present()
    test_synthesizer_no_warning_for_a_deliberate_small_limit()
