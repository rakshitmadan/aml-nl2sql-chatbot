"""Phase 4: the AML Policy Engine. No LLM anywhere in this file — every
finding is produced by a plain Python/pandas function so results are
reproducible, explainable, and auditable, the way real compliance logic has
to be. This is deliberately decoupled from the LLM/graph: any DataFrame with
the right columns can be passed in, whether it came from a full DB dump (as
in this file's own CLI/tests) or from whatever the NL->SQL graph retrieved.
"""

import sqlite3
from dataclasses import asdict, dataclass

import pandas as pd

CTR_THRESHOLD = 10000

STRUCTURING_MIN = 9000
STRUCTURING_MAX = 9999.99
STRUCTURING_WINDOW_HOURS = 48
STRUCTURING_MIN_COUNT = 3

VELOCITY_WINDOW_DAYS = 3
VELOCITY_MIN_ABS_COUNT = 10       # never flag below this many txns, regardless of baseline
VELOCITY_SPIKE_MULTIPLIER = 3     # window count must exceed baseline-implied count by this much

def get_high_risk_countries(db_path: str = "db/aml.db") -> set[str]:
    """Load active high-risk countries from database (dynamic, not hardcoded)."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT country FROM high_risk_countries WHERE is_active = 1")
        return {row[0] for row in cur.fetchall()}
    except sqlite3.OperationalError:
        # Fallback if table doesn't exist (for backwards compatibility)
        return {"Nigeria", "Panama", "Cayman Islands"}
    finally:
        conn.close()

REQUIRED_COLUMNS = {
    "transaction_id",
    "account_id",
    "amount",
    "txn_date",
    "counterparty_name",
    "counterparty_country",
    "counterparty_is_pep",
    "counterparty_is_sanctioned",
}

OPTIONAL_COLUMNS_FOR_KYC = {
    "customer_kyc_status",
}  # Will only be used if present in the DataFrame


@dataclass
class Finding:
    transaction_id: int
    rule_triggered: str
    severity: str  # low | medium | high
    explanation: str

    def as_dict(self) -> dict:
        return asdict(self)


def _check_columns(df: pd.DataFrame) -> None:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"policy engine input is missing required columns: {sorted(missing)}")


def _sliding_window_clusters(
    timestamps: pd.Series, window: pd.Timedelta, min_count: int
) -> list[list[int]]:
    """Given a time-sorted series (positional index 0..n-1), returns groups
    of positional indices where every index in the group falls inside some
    window-wide time window containing at least min_count entries.
    Overlapping windows are merged into a single contiguous cluster.
    """
    n = len(timestamps)
    flagged: set[int] = set()
    left = 0
    for right in range(n):
        while timestamps.iloc[right] - timestamps.iloc[left] > window:
            left += 1
        if right - left + 1 >= min_count:
            flagged.update(range(left, right + 1))

    if not flagged:
        return []

    ordered = sorted(flagged)
    clusters, current = [], [ordered[0]]
    for idx in ordered[1:]:
        if idx == current[-1] + 1:
            current.append(idx)
        else:
            clusters.append(current)
            current = [idx]
    clusters.append(current)
    return clusters


def check_ctr_threshold(df: pd.DataFrame) -> list[Finding]:
    hits = df[df["amount"] > CTR_THRESHOLD]
    return [
        Finding(
            transaction_id=int(row.transaction_id),
            rule_triggered="ctr_threshold",
            severity="high",
            explanation=(
                f"Single transaction of ${row.amount:,.2f} exceeds the "
                f"${CTR_THRESHOLD:,} CTR reporting threshold."
            ),
        )
        for row in hits.itertuples()
    ]


def check_structuring(df: pd.DataFrame) -> list[Finding]:
    findings = []
    candidates = df[(df["amount"] >= STRUCTURING_MIN) & (df["amount"] <= STRUCTURING_MAX)]
    window = pd.Timedelta(hours=STRUCTURING_WINDOW_HOURS)

    for account_id, group in candidates.groupby("account_id"):
        group = group.sort_values("txn_date").reset_index(drop=True)
        clusters = _sliding_window_clusters(group["txn_date"], window, STRUCTURING_MIN_COUNT)
        for cluster in clusters:
            cluster_ids = group.loc[cluster, "transaction_id"].tolist()
            for idx in cluster:
                findings.append(
                    Finding(
                        transaction_id=int(group.loc[idx, "transaction_id"]),
                        rule_triggered="structuring",
                        severity="high",
                        explanation=(
                            f"One of {len(cluster_ids)} transactions on account {account_id} "
                            f"between ${STRUCTURING_MIN:,.0f}-${STRUCTURING_MAX:,.2f} within "
                            f"{STRUCTURING_WINDOW_HOURS}h (transaction_ids: {cluster_ids}), "
                            "consistent with structuring/smurfing."
                        ),
                    )
                )
    return findings


def check_velocity(df: pd.DataFrame) -> list[Finding]:
    """Flags a burst relative to the account's *own* historical rate, not a
    flat count — an account that normally does 20 txns/day isn't spiking at
    20 txns in 3 days, but an account that normally does 1/day is."""
    findings = []
    window = pd.Timedelta(days=VELOCITY_WINDOW_DAYS)

    for account_id, group in df.groupby("account_id"):
        group = group.sort_values("txn_date").reset_index(drop=True)
        n = len(group)
        if n < VELOCITY_MIN_ABS_COUNT:
            continue

        span_days = (group["txn_date"].max() - group["txn_date"].min()).total_seconds() / 86400
        avg_daily_rate = n / span_days if span_days > 0 else n
        expected_in_window = avg_daily_rate * VELOCITY_WINDOW_DAYS
        spike_threshold = max(VELOCITY_MIN_ABS_COUNT, VELOCITY_SPIKE_MULTIPLIER * expected_in_window)

        clusters = _sliding_window_clusters(group["txn_date"], window, spike_threshold)
        for cluster in clusters:
            cluster_ids = group.loc[cluster, "transaction_id"].tolist()
            for idx in cluster:
                findings.append(
                    Finding(
                        transaction_id=int(group.loc[idx, "transaction_id"]),
                        rule_triggered="velocity_spike",
                        severity="medium",
                        explanation=(
                            f"Account {account_id} normally averages ~{avg_daily_rate:.1f} "
                            f"txns/day, but {len(cluster_ids)} transactions occurred within a "
                            f"{VELOCITY_WINDOW_DAYS}-day window (transaction_ids: {cluster_ids}) "
                            "— a spike relative to its own baseline."
                        ),
                    )
                )
    return findings


def check_sanctions_and_pep(df: pd.DataFrame) -> list[Finding]:
    findings = []
    sanctioned = df[df["counterparty_is_sanctioned"] == 1]
    for row in sanctioned.itertuples():
        findings.append(
            Finding(
                transaction_id=int(row.transaction_id),
                rule_triggered="sanctions_match",
                severity="high",
                explanation=(
                    f"Counterparty '{row.counterparty_name}' ({row.counterparty_country}) "
                    "appears on a sanctions list."
                ),
            )
        )

    pep_only = df[(df["counterparty_is_pep"] == 1) & (df["counterparty_is_sanctioned"] == 0)]
    for row in pep_only.itertuples():
        findings.append(
            Finding(
                transaction_id=int(row.transaction_id),
                rule_triggered="pep_match",
                severity="medium",
                explanation=(
                    f"Counterparty '{row.counterparty_name}' ({row.counterparty_country}) is a "
                    "politically exposed person (PEP)."
                ),
            )
        )
    return findings


def check_high_risk_jurisdiction(df: pd.DataFrame) -> list[Finding]:
    high_risk_countries = get_high_risk_countries()
    hits = df[df["counterparty_country"].isin(high_risk_countries)]
    return [
        Finding(
            transaction_id=int(row.transaction_id),
            rule_triggered="high_risk_jurisdiction",
            severity="low",
            explanation=(
                f"Counterparty is located in {row.counterparty_country}, "
                "considered a high-risk jurisdiction."
            ),
        )
        for row in hits.itertuples()
    ]


def check_incomplete_kyc(df: pd.DataFrame) -> list[Finding]:
    """Flags transactions from customers with incomplete, rejected, or expired KYC.
    Only runs if the DataFrame includes customer_kyc_status column."""
    if "customer_kyc_status" not in df.columns:
        return []

    # Flag transactions from customers with non-verified KYC
    incomplete_kyc = df[df["customer_kyc_status"].isin(["pending", "rejected", "expired"])]
    return [
        Finding(
            transaction_id=int(row.transaction_id),
            rule_triggered="incomplete_kyc",
            severity="medium",
            explanation=(
                f"Transaction from customer with KYC status '{row.customer_kyc_status}'. "
                "Enhanced monitoring required until KYC verification is complete."
            ),
        )
        for row in incomplete_kyc.itertuples()
    ]


ALL_RULES = [
    check_ctr_threshold,
    check_structuring,
    check_velocity,
    check_sanctions_and_pep,
    check_high_risk_jurisdiction,
    check_incomplete_kyc,
]


def run_policy_engine(df: pd.DataFrame) -> list[Finding]:
    _check_columns(df)
    df = df.copy()
    df["txn_date"] = pd.to_datetime(df["txn_date"])
    findings: list[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(df))
    return findings


def load_transactions_df(db_path: str = "db/aml.db", transaction_ids: list[int] | None = None) -> pd.DataFrame:
    """Loads transactions joined with their counterparty and customer (for KYC),
    in the exact shape the policy engine needs. Optionally scoped to a specific
    set of transaction_ids.

    The graph node in `app/graph.py` uses the `transaction_ids` filter
    rather than trusting the SQL Executor's raw result columns directly —
    the NL->SQL generator picks its own column aliases per question (e.g.
    `is_pep` vs `counterparty_is_pep`), which the policy engine can't rely
    on staying consistent. Re-querying by ID here guarantees the policy
    engine always gets its required columns in its required shape,
    regardless of how the original question was phrased or joined.
    """
    conn = sqlite3.connect(db_path)
    query = """
        SELECT
            t.transaction_id, t.account_id, t.amount, t.txn_date,
            c.name AS counterparty_name, c.country AS counterparty_country,
            c.is_pep AS counterparty_is_pep, c.is_sanctioned AS counterparty_is_sanctioned,
            cu.kyc_status AS customer_kyc_status
        FROM transactions t
        JOIN counterparties c ON t.counterparty_id = c.counterparty_id
        JOIN accounts a ON t.account_id = a.account_id
        JOIN customers cu ON a.customer_id = cu.customer_id
    """
    params: list = []
    if transaction_ids is not None:
        placeholders = ",".join("?" * len(transaction_ids))
        query += f" WHERE t.transaction_id IN ({placeholders})"
        params = list(transaction_ids)

    df = pd.read_sql(query, conn, params=params, parse_dates=["txn_date"])
    conn.close()
    return df


def main():
    df = load_transactions_df()
    findings = run_policy_engine(df)

    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f.rule_triggered] = by_rule.get(f.rule_triggered, 0) + 1

    print(f"Ran policy engine over {len(df)} transactions.")
    print(f"Total findings: {len(findings)}\n")
    for rule, count in sorted(by_rule.items()):
        print(f"  {rule}: {count}")

    print("\nSample findings:")
    for f in findings[:5]:
        print(f"  [{f.severity:6}] txn {f.transaction_id} — {f.rule_triggered}: {f.explanation}")


if __name__ == "__main__":
    main()
