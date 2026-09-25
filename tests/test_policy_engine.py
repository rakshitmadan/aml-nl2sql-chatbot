"""Checks the Phase 4 policy engine against ground truth we already know
exists in the seeded database (same approach as tests/verify_seed.py, one
level up: verify the *rules*, not just the raw SQL patterns).

Run with: ./venv/bin/python -m tests.test_policy_engine
"""

from app.policy_engine import load_transactions_df, run_policy_engine

EXPECTED_SANCTIONED_TXN_IDS = {2839, 2840, 2841, 2842, 2843, 2844}
EXPECTED_CTR_TXN_IDS = {2829, 2830, 2831, 2832, 2833, 2834, 2835, 2836, 2837, 2838, 2842, 2843}


def test_ctr_and_sanctions_match_ground_truth():
    df = load_transactions_df()
    findings = run_policy_engine(df)

    ctr_ids = {f.transaction_id for f in findings if f.rule_triggered == "ctr_threshold"}
    sanctions_ids = {f.transaction_id for f in findings if f.rule_triggered == "sanctions_match"}

    assert ctr_ids == EXPECTED_CTR_TXN_IDS, f"CTR mismatch: {ctr_ids ^ EXPECTED_CTR_TXN_IDS}"
    assert sanctions_ids == EXPECTED_SANCTIONED_TXN_IDS, (
        f"sanctions mismatch: {sanctions_ids ^ EXPECTED_SANCTIONED_TXN_IDS}"
    )
    print("PASS: ctr_threshold and sanctions_match findings match ground truth exactly")


def test_structuring_finds_eight_accounts():
    df = load_transactions_df()
    findings = run_policy_engine(df)

    structuring = [f for f in findings if f.rule_triggered == "structuring"]
    accounts_flagged = {df.loc[df["transaction_id"] == f.transaction_id, "account_id"].iloc[0] for f in structuring}

    assert len(accounts_flagged) == 8, f"expected 8 structuring accounts, got {len(accounts_flagged)}"
    assert all(3 <= sum(1 for f in structuring if df.loc[df["transaction_id"] == f.transaction_id, "account_id"].iloc[0] == a) <= 4 for a in accounts_flagged)
    print(f"PASS: structuring correctly flags exactly 8 accounts ({len(structuring)} transactions total)")


def test_velocity_finds_exactly_one_account():
    df = load_transactions_df()
    findings = run_policy_engine(df)

    velocity = [f for f in findings if f.rule_triggered == "velocity_spike"]
    accounts_flagged = {df.loc[df["transaction_id"] == f.transaction_id, "account_id"].iloc[0] for f in velocity}

    assert len(accounts_flagged) == 1, f"expected exactly 1 velocity-spike account, got {accounts_flagged}"
    print(f"PASS: velocity_spike correctly flags exactly 1 account ({len(velocity)} transactions)")


def test_jurisdiction_and_pep_rates_are_realistic():
    """Regression guard against the Bug 2 scenario: these rules should flag
    a small minority of transactions, not a quarter of the whole database."""
    df = load_transactions_df()
    findings = run_policy_engine(df)
    total = len(df)

    jurisdiction_rate = sum(1 for f in findings if f.rule_triggered == "high_risk_jurisdiction") / total
    pep_rate = sum(1 for f in findings if f.rule_triggered == "pep_match") / total

    assert jurisdiction_rate < 0.15, f"jurisdiction rate too high: {jurisdiction_rate:.1%} (alert-fatigue regression)"
    assert pep_rate < 0.10, f"PEP rate too high: {pep_rate:.1%} (alert-fatigue regression)"
    print(f"PASS: jurisdiction rate {jurisdiction_rate:.1%}, PEP rate {pep_rate:.1%} — both realistic")


if __name__ == "__main__":
    test_ctr_and_sanctions_match_ground_truth()
    test_structuring_finds_eight_accounts()
    test_velocity_finds_exactly_one_account()
    test_jurisdiction_and_pep_rates_are_realistic()
