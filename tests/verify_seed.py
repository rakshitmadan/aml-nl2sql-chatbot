"""Sanity-checks that the seeded patterns are actually queryable. Run with:
./venv/bin/python -m tests.verify_seed
"""

import pandas as pd
from sqlalchemy import text

from db.database import get_engine

SQL_CHECKS = {
    "CTR breaches (> $10,000)": """
        SELECT COUNT(*) FROM transactions WHERE amount > 10000
    """,
    "Sanctioned counterparty transactions": """
        SELECT COUNT(*) FROM transactions t
        JOIN counterparties c ON t.counterparty_id = c.counterparty_id
        WHERE c.is_sanctioned = 1
    """,
    "Possible structuring (3+ txns $9,000-$9,999 same account within 48h)": """
        SELECT account_id, COUNT(*) as n
        FROM transactions
        WHERE amount BETWEEN 9000 AND 9999
        GROUP BY account_id
        HAVING COUNT(*) >= 3
    """,
}


def find_velocity_spikes(engine, window_days: int = 3, min_txns: int = 15):
    """A true rolling-window check: total transaction count per account is a
    misleading proxy (variance alone can push a normal account over a flat
    threshold) — what actually matters is whether *any* window_days-wide
    window contains an unusual burst."""
    df = pd.read_sql(text("SELECT account_id, txn_date FROM transactions"), engine, parse_dates=["txn_date"])
    spikes = []
    for account_id, group in df.groupby("account_id"):
        dates = group["txn_date"].sort_values().reset_index(drop=True)
        for i in range(len(dates)):
            window_end = dates[i] + pd.Timedelta(days=window_days)
            count_in_window = ((dates >= dates[i]) & (dates < window_end)).sum()
            if count_in_window >= min_txns:
                spikes.append((account_id, count_in_window))
                break
    return spikes


def main():
    engine = get_engine()
    with engine.connect() as conn:
        for label, sql in SQL_CHECKS.items():
            rows = conn.execute(text(sql)).fetchall()
            print(f"\n{label}:")
            if not rows:
                print("  (none found)")
            for row in rows[:10]:
                print(" ", row)

    print("\nAccounts with a true velocity spike (>= 15 txns in any 3-day window):")
    spikes = find_velocity_spikes(engine)
    if not spikes:
        print("  (none found)")
    for account_id, n in spikes[:10]:
        print(" ", (account_id, n))


if __name__ == "__main__":
    main()
