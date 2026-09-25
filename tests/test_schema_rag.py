"""Regression guard for schema retrieval quality. An earlier version of the
table descriptions failed this: 'counterparties' — essential for judging
suspicion (is_pep, is_sanctioned, country) — didn't rank in the top 5 or
even top 6 of 8 tables for the flagship "which are suspicious" question,
because MiniLM's semantic match favored 'sar_filings' (literally contains
the word "Suspicious") over the table that actually carries the risk
signals. Fixed by enriching descriptions with representative question
phrasings. This test locks that fix in.

Run with: ./venv/bin/python -m tests.test_schema_rag
"""

from app.schema_rag import build_index, retrieve_relevant_tables

K = 5


def test_flagship_question_retrieves_transactions_and_counterparties():
    build_index(force=True)
    tables = retrieve_relevant_tables(
        "show me last month's transactions and tell me which are suspicious", k=K
    )
    assert "transactions" in tables, f"transactions missing from {tables}"
    assert "counterparties" in tables, (
        f"counterparties missing from {tables} — this is the table with is_pep/"
        "is_sanctioned/country, essential for judging suspicion"
    )
    print(f"PASS: flagship question retrieves both transactions and counterparties (top {K}: {tables})")


def test_sanctions_question_retrieves_counterparties_and_sanctions_list():
    tables = retrieve_relevant_tables("which counterparties are sanctioned", k=K)
    assert "counterparties" in tables
    assert "sanctions_list" in tables
    print(f"PASS: sanctions question retrieves counterparties + sanctions_list (top {K}: {tables})")


def test_sar_question_retrieves_sar_filings():
    tables = retrieve_relevant_tables("which customers have filed SARs", k=K)
    assert "sar_filings" in tables
    assert "customers" in tables
    print(f"PASS: SAR question retrieves sar_filings + customers (top {K}: {tables})")


if __name__ == "__main__":
    test_flagship_question_retrieves_transactions_and_counterparties()
    test_sanctions_question_retrieves_counterparties_and_sanctions_list()
    test_sar_question_retrieves_sar_filings()
