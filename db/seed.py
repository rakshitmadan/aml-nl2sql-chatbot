"""Generates synthetic AML data: realistic noise plus deliberately-planted
suspicious patterns (structuring, CTR breaches, sanctioned counterparties,
a velocity spike) so downstream rules have real findings to surface.

Run with: ./venv/bin/python -m db.seed
"""

import random
from datetime import date, datetime, timedelta

from faker import Faker

from db.database import get_engine, get_session, init_db
from db.models import (
    Account,
    Alert,
    Counterparty,
    Customer,
    HighRiskCountry,
    KYCDocument,
    PepListEntry,
    SanctionsListEntry,
    SarFiling,
    Transaction,
)

SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

NOW = datetime.now()
LAST_MONTH_START = (NOW.replace(day=1) - timedelta(days=1)).replace(day=1)
LAST_MONTH_END = NOW.replace(day=1) - timedelta(seconds=1)

RISK_RATINGS = ["low", "medium", "high"]
RISK_WEIGHTS = [0.7, 0.22, 0.08]
COUNTRIES = ["USA", "UK", "Germany", "UAE", "Singapore", "Nigeria", "Panama", "Cayman Islands", "India", "Brazil"]
HIGH_RISK_COUNTRIES = ["Nigeria", "Panama", "Cayman Islands"]

# Country-to-Faker locale mapping for realistic name generation
COUNTRY_LOCALE_MAP = {
    "USA": "en_US",
    "UK": "en_GB",
    "Germany": "de_DE",
    "UAE": "ar_SA",  # Use Saudi Arabic for Middle East
    "Singapore": "en_US",  # English-speaking but use US as fallback
    "Nigeria": "en_US",  # English-speaking but use US as fallback
    "Panama": "es_ES",  # Use Spanish
    "Cayman Islands": "en_GB",  # Use UK English for Caribbean
    "India": "en_IN",  # Use Indian English (more supported than hi_IN)
    "Brazil": "pt_BR",
}


def get_name_generator(country: str):
    """Get a Faker instance with locale appropriate for the country."""
    locale = COUNTRY_LOCALE_MAP.get(country, "en_US")
    faker_instance = Faker(locale)
    Faker.seed(SEED)  # Keep deterministic
    return faker_instance


def random_datetime_between(start: datetime, end: datetime) -> datetime:
    delta = end - start
    seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=seconds)


def create_customers(n: int) -> list[Customer]:
    customers = []
    for i in range(1, n + 1):
        country = random.choice(COUNTRIES)
        faker_for_country = get_name_generator(country)
        customers.append(
            Customer(
                customer_id=i,
                name=faker_for_country.name(),
                dob=faker_for_country.date_of_birth(minimum_age=18, maximum_age=85),
                risk_rating=random.choices(RISK_RATINGS, weights=RISK_WEIGHTS)[0],
                country=country,
                onboarding_date=faker_for_country.date_between(start_date="-8y", end_date="-30d"),
            )
        )
    return customers


def create_accounts(customers: list[Customer]) -> list[Account]:
    accounts = []
    account_id = 1
    for customer in customers:
        for _ in range(random.randint(1, 2)):
            accounts.append(
                Account(
                    account_id=account_id,
                    customer_id=customer.customer_id,
                    account_type=random.choice(["checking", "savings", "business"]),
                    open_date=fake.date_between(start_date=customer.onboarding_date, end_date="-10d"),
                    status="active",
                )
            )
            account_id += 1
    return accounts


def create_counterparties(n: int, n_sanctioned: int, n_pep: int) -> list[Counterparty]:
    counterparties = []
    sanctioned_idx = set(random.sample(range(n), n_sanctioned))
    pep_idx = set(random.sample([i for i in range(n) if i not in sanctioned_idx], n_pep))
    for i in range(n):
        is_sanctioned = i in sanctioned_idx
        cp_country = random.choice(HIGH_RISK_COUNTRIES) if is_sanctioned else random.choice(COUNTRIES)
        faker_for_country = get_name_generator(cp_country)

        is_company = random.random() < 0.4
        if is_company:
            cp_name = faker_for_country.company()
        else:
            cp_name = faker_for_country.name()

        counterparties.append(
            Counterparty(
                counterparty_id=i + 1,
                name=cp_name,
                country=cp_country,
                is_pep=i in pep_idx,
                is_sanctioned=is_sanctioned,
            )
        )
    return counterparties


def create_watchlists(counterparties: list[Counterparty]) -> tuple[list[SanctionsListEntry], list[PepListEntry]]:
    sanctions, peps = [], []
    entry_id = 1
    for cp in counterparties:
        if cp.is_sanctioned:
            sanctions.append(
                SanctionsListEntry(
                    entry_id=entry_id,
                    name=cp.name,
                    country=cp.country,
                    list_source=random.choice(["OFAC", "UN", "EU"]),
                    date_added=fake.date_between(start_date="-5y", end_date="-1y"),
                )
            )
            entry_id += 1
    entry_id = 1
    for cp in counterparties:
        if cp.is_pep:
            peps.append(
                PepListEntry(
                    entry_id=entry_id,
                    name=cp.name,
                    country=cp.country,
                    list_source="World-Check",
                    date_added=fake.date_between(start_date="-5y", end_date="-1y"),
                )
            )
            entry_id += 1
    return sanctions, peps


ELEVATED_COUNTERPARTY_RATE = 0.08


def bucket_counterparties(counterparties: list[Counterparty]) -> tuple[list[Counterparty], list[Counterparty]]:
    """Splits non-sanctioned counterparties into 'low_risk' (not PEP, not in
    a high-risk country) and 'elevated' (PEP and/or high-risk-country).
    Sanctioned counterparties are handled entirely separately.

    This matters because country and PEP status were assigned uniformly at
    random when counterparties were created — 3 of 10 countries are
    'high-risk', so ~27% of ordinary counterparties end up in one purely by
    chance. If noise transactions picked counterparties uniformly too,
    ~27% of *all* transactions would trip the jurisdiction rule for no
    behavioral reason, drowning out real signal (confirmed empirically: an
    earlier version of this generator produced 790 jurisdiction findings
    and 234 PEP findings out of 2,863 transactions when tested against the
    Phase 4 policy engine). Real transaction volume shouldn't touch
    elevated-risk counterparties at anywhere near their raw population
    share, so noise transactions instead pick from 'elevated' at a fixed,
    low, realistic rate.
    """
    low_risk = [
        cp for cp in counterparties
        if not cp.is_sanctioned and not cp.is_pep and cp.country not in HIGH_RISK_COUNTRIES
    ]
    elevated = [
        cp for cp in counterparties
        if not cp.is_sanctioned and (cp.is_pep or cp.country in HIGH_RISK_COUNTRIES)
    ]
    return low_risk, elevated


def pick_counterparty(low_risk: list[Counterparty], elevated: list[Counterparty]) -> Counterparty:
    if elevated and random.random() < ELEVATED_COUNTERPARTY_RATE:
        return random.choice(elevated)
    return random.choice(low_risk)


def create_noise_transactions(
    accounts: list[Account], counterparties: list[Counterparty], n: int, start_id: int
) -> list[Transaction]:
    low_risk, elevated = bucket_counterparties(counterparties)
    txns = []
    for i in range(n):
        txns.append(
            Transaction(
                transaction_id=start_id + i,
                account_id=random.choice(accounts).account_id,
                counterparty_id=pick_counterparty(low_risk, elevated).counterparty_id,
                amount=round(random.uniform(20, 8000), 2),
                currency="USD",
                txn_date=random_datetime_between(NOW - timedelta(days=180), NOW),
                txn_type=random.choice(["ach", "card", "check", "wire"]),
                direction=random.choice(["inbound", "outbound"]),
            )
        )
    return txns


def create_structuring_sequences(
    accounts: list[Account], counterparties: list[Counterparty], n_sequences: int, start_id: int
) -> list[Transaction]:
    """Classic smurfing pattern: several cash-outs just under the $10k CTR
    threshold, clustered within a short window, on the same account."""
    low_risk, _ = bucket_counterparties(counterparties)
    txns = []
    txn_id = start_id
    chosen_accounts = random.sample(accounts, n_sequences)
    for account in chosen_accounts:
        window_start = random_datetime_between(LAST_MONTH_START, LAST_MONTH_END - timedelta(days=2))
        n_in_sequence = random.randint(3, 4)
        for _ in range(n_in_sequence):
            offset = timedelta(hours=random.randint(0, 40))
            txns.append(
                Transaction(
                    transaction_id=txn_id,
                    account_id=account.account_id,
                    counterparty_id=random.choice(low_risk).counterparty_id,
                    amount=round(random.uniform(9000, 9900), 2),
                    currency="USD",
                    txn_date=window_start + offset,
                    txn_type="cash",
                    direction="outbound",
                )
            )
            txn_id += 1
    return txns


def create_large_transactions(
    accounts: list[Account], counterparties: list[Counterparty], n: int, start_id: int
) -> list[Transaction]:
    """Single transactions over the $10,000 CTR reporting threshold."""
    low_risk, _ = bucket_counterparties(counterparties)
    txns = []
    for i in range(n):
        txns.append(
            Transaction(
                transaction_id=start_id + i,
                account_id=random.choice(accounts).account_id,
                counterparty_id=random.choice(low_risk).counterparty_id,
                amount=round(random.uniform(10001, 45000), 2),
                currency="USD",
                txn_date=random_datetime_between(LAST_MONTH_START, LAST_MONTH_END),
                txn_type="wire",
                direction="outbound",
            )
        )
    return txns


def create_sanctioned_counterparty_transactions(
    accounts: list[Account], counterparties: list[Counterparty], n: int, start_id: int
) -> list[Transaction]:
    sanctioned_cps = [cp for cp in counterparties if cp.is_sanctioned]
    txns = []
    for i in range(n):
        txns.append(
            Transaction(
                transaction_id=start_id + i,
                account_id=random.choice(accounts).account_id,
                counterparty_id=random.choice(sanctioned_cps).counterparty_id,
                amount=round(random.uniform(500, 25000), 2),
                currency="USD",
                txn_date=random_datetime_between(LAST_MONTH_START, LAST_MONTH_END),
                txn_type="wire",
                direction="outbound",
            )
        )
    return txns


def create_velocity_spike(
    accounts: list[Account], counterparties: list[Counterparty], start_id: int
) -> list[Transaction]:
    """One account that's normally quiet suddenly makes many transactions in
    a few days — a spike relative to its own baseline, not a single big
    number, so it needs a different rule than the threshold check."""
    low_risk, _ = bucket_counterparties(counterparties)
    account = random.choice(accounts)
    spike_start = random_datetime_between(LAST_MONTH_START, LAST_MONTH_END - timedelta(days=3))
    txns = []
    for i in range(18):
        txns.append(
            Transaction(
                transaction_id=start_id + i,
                account_id=account.account_id,
                counterparty_id=random.choice(low_risk).counterparty_id,
                amount=round(random.uniform(300, 4000), 2),
                currency="USD",
                txn_date=spike_start + timedelta(hours=random.randint(0, 72)),
                txn_type=random.choice(["ach", "wire"]),
                direction="outbound",
            )
        )
    return txns


def create_historical_alerts(transactions: list[Transaction], n: int) -> list[Alert]:
    """Pre-existing alerts already in the bank's alerting system — separate
    from the live AML Policy Engine we'll build in Phase 4, which computes
    findings fresh from whatever the chatbot just queried."""
    sample = random.sample(transactions, n)
    alerts = []
    for i, txn in enumerate(sample, start=1):
        alerts.append(
            Alert(
                alert_id=i,
                transaction_id=txn.transaction_id,
                rule_triggered=random.choice(
                    ["ctr_threshold", "structuring", "sanctions_match", "velocity_spike"]
                ),
                severity=random.choice(["low", "medium", "high"]),
                created_date=txn.txn_date + timedelta(hours=random.randint(1, 48)),
                status=random.choice(["open", "closed", "escalated"]),
            )
        )
    return alerts


def create_sar_filings(customers: list[Customer], accounts: list[Account], n: int) -> list[SarFiling]:
    account_by_customer = {}
    for acc in accounts:
        account_by_customer.setdefault(acc.customer_id, []).append(acc)
    eligible_customers = random.sample(customers, n)
    filings = []
    for i, customer in enumerate(eligible_customers, start=1):
        filings.append(
            SarFiling(
                sar_id=i,
                customer_id=customer.customer_id,
                filed_date=fake.date_between(start_date="-1y", end_date="today"),
                narrative_summary=(
                    f"Unusual transaction activity identified for {customer.name}; "
                    "pattern consistent with potential structuring. Filed per policy."
                ),
                status=random.choice(["filed", "under_review", "closed"]),
            )
        )
    return filings


def create_high_risk_countries() -> list[HighRiskCountry]:
    """Create a dynamic high-risk countries list instead of hardcoding."""
    countries_data = [
        ("Nigeria", "OFAC sanctions and money laundering concerns"),
        ("Panama", "Financial secrecy and sanctions evasion hub"),
        ("Cayman Islands", "Offshore financial center with lax regulations"),
        ("North Korea", "OFAC comprehensive sanctions"),
        ("Iran", "OFAC comprehensive sanctions"),
        ("Syria", "OFAC comprehensive sanctions"),
        ("Venezuela", "OFAC sanctions"),
    ]
    entries = []
    for i, (country, reason) in enumerate(countries_data, start=1):
        entries.append(
            HighRiskCountry(
                country_id=i,
                country=country,
                reason=reason,
                date_added=fake.date_between(start_date="-5y", end_date="-1y"),
                is_active=True,
            )
        )
    return entries


def add_kyc_to_customers(customers: list[Customer]) -> None:
    """Add KYC status to customers in-place."""
    kyc_statuses = ["verified", "pending", "rejected", "expired"]
    kyc_weights = [0.7, 0.15, 0.10, 0.05]
    for customer in customers:
        customer.kyc_status = random.choices(kyc_statuses, weights=kyc_weights)[0]
        if customer.kyc_status == "verified":
            customer.kyc_completion_date = fake.date_between(
                start_date=customer.onboarding_date, end_date="-30d"
            )
            customer.kyc_level = random.choice([2, 3])
        elif customer.kyc_status == "expired":
            customer.kyc_completion_date = fake.date_between(start_date="-5y", end_date="-2y")
            customer.kyc_level = random.choice([2, 3])
        else:
            customer.kyc_level = random.choice([0, 1])


def create_kyc_documents(customers: list[Customer]) -> list[KYCDocument]:
    """Create KYC documents for customers with verified or pending status."""
    docs = []
    doc_id = 1
    doc_types = ["passport", "driver_license", "national_id", "utility_bill"]

    for customer in customers:
        if customer.kyc_status in ["verified", "pending", "rejected"]:
            n_docs = random.randint(1, 2)
            for _ in range(n_docs):
                doc_type = random.choice(doc_types)
                issue_date = fake.date_between(start_date="-10y", end_date="-1y")
                expiry_date = issue_date + timedelta(days=random.randint(365 * 5, 365 * 10))

                docs.append(
                    KYCDocument(
                        doc_id=doc_id,
                        customer_id=customer.customer_id,
                        doc_type=doc_type,
                        doc_number=fake.bothify(text="##-???-###", letters="ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                        issue_date=issue_date,
                        expiry_date=expiry_date if random.random() < 0.9 else None,
                        issuing_country=customer.country,
                        verified_date=(
                            customer.kyc_completion_date
                            if customer.kyc_status == "verified"
                            else None
                        ),
                        verification_status=(
                            "verified" if customer.kyc_status == "verified" else
                            "rejected" if customer.kyc_status == "rejected" else
                            "pending"
                        ),
                    )
                )
                doc_id += 1

    return docs


def main():
    engine = get_engine()
    init_db(engine)

    customers = create_customers(150)
    add_kyc_to_customers(customers)  # Add KYC status to customers
    kyc_documents = create_kyc_documents(customers)
    high_risk_countries = create_high_risk_countries()

    accounts = create_accounts(customers)
    counterparties = create_counterparties(120, n_sanctioned=7, n_pep=9)
    sanctions_list, pep_list = create_watchlists(counterparties)

    next_id = 1
    noise = create_noise_transactions(accounts, counterparties, n=2800, start_id=next_id)
    next_id += len(noise)
    structuring = create_structuring_sequences(accounts, counterparties, n_sequences=8, start_id=next_id)
    next_id += len(structuring)
    large = create_large_transactions(accounts, counterparties, n=10, start_id=next_id)
    next_id += len(large)
    sanctioned = create_sanctioned_counterparty_transactions(accounts, counterparties, n=6, start_id=next_id)
    next_id += len(sanctioned)
    spike = create_velocity_spike(accounts, counterparties, start_id=next_id)
    next_id += len(spike)

    all_transactions = noise + structuring + large + sanctioned + spike
    alerts = create_historical_alerts(all_transactions, n=40)
    sar_filings = create_sar_filings(customers, accounts, n=15)

    # Collect KYC stats before closing session
    kyc_stats = {
        "verified": sum(1 for c in customers if c.kyc_status == "verified"),
        "pending": sum(1 for c in customers if c.kyc_status == "pending"),
        "rejected": sum(1 for c in customers if c.kyc_status == "rejected"),
        "expired": sum(1 for c in customers if c.kyc_status == "expired"),
    }

    with get_session(engine) as session:
        session.add_all(customers)
        session.add_all(accounts)
        session.add_all(counterparties)
        session.add_all(sanctions_list)
        session.add_all(pep_list)
        session.add_all(high_risk_countries)
        session.add_all(kyc_documents)
        session.add_all(all_transactions)
        session.add_all(alerts)
        session.add_all(sar_filings)
        session.commit()

    print("Seed complete.")
    print(f"  customers:            {len(customers)}")
    print(f"    - KYC verified:     {kyc_stats['verified']}")
    print(f"    - KYC pending:      {kyc_stats['pending']}")
    print(f"    - KYC rejected:     {kyc_stats['rejected']}")
    print(f"    - KYC expired:      {kyc_stats['expired']}")
    print(f"  accounts:             {len(accounts)}")
    print(f"  counterparties:       {len(counterparties)}  ({7} sanctioned, {9} PEP)")
    print(f"  transactions:         {len(all_transactions)}")
    print(f"    - noise:                {len(noise)}")
    print(f"    - structuring seqs:     {len(structuring)}  ({8} sequences)")
    print(f"    - large (CTR breach):   {len(large)}")
    print(f"    - sanctioned cp hits:   {len(sanctioned)}")
    print(f"    - velocity spike:       {len(spike)}  (1 account)")
    print(f"  alerts (historical):  {len(alerts)}")
    print(f"  sar_filings:          {len(sar_filings)}")
    print(f"  kyc_documents:        {len(kyc_documents)}")
    print(f"  high_risk_countries:  {len(high_risk_countries)}")


if __name__ == "__main__":
    main()
