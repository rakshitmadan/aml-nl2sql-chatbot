"""Schema descriptions fed into the NL->SQL prompt.

Split into one description per table (`TABLE_DESCRIPTIONS`) so Phase 5's
schema-retrieval node can embed and retrieve them individually, rather than
always dumping the entire schema into every prompt. `SCHEMA_DESCRIPTION`
(the full join of all of them) is kept as a fallback for callers that don't
use retrieval — e.g. the Phase 2 prototype script.
"""

TABLE_DESCRIPTIONS: dict[str, str] = {
    "customers": """
customers(customer_id, name, dob, risk_rating, country, onboarding_date)
  - a bank customer/client
  - risk_rating is one of: low, medium, high
""".strip(),
    "accounts": """
accounts(account_id, customer_id, account_type, open_date, status)
  - a customer's bank account (checking/savings/business)
  - account_type is one of: checking, savings, business
  - customer_id references customers.customer_id
""".strip(),
    "counterparties": """
counterparties(counterparty_id, name, country, is_pep, is_sanctioned)
  - the other party on a transaction (who money is sent to or received from)
  - is_pep, is_sanctioned are booleans (0/1) — a "sanctioned" or "PEP
    (politically exposed person)" counterparty is a red flag, relevant to
    identifying suspicious or risky transactions, sanctions screening, and
    high-risk jurisdiction checks
  - answers questions like: "which counterparties are sanctioned/PEP",
    "who did this account send money to", "risky counterparties by country"
""".strip(),
    "transactions": """
transactions(transaction_id, account_id, counterparty_id, amount, currency,
             txn_date, txn_type, direction)
  - a single money movement: wires, ACH, cash, checks, card transactions
  - account_id references accounts.account_id
  - counterparty_id references counterparties.counterparty_id
  - txn_type is one of: wire, ach, cash, check, card
  - direction is one of: inbound, outbound
  - txn_date is a full datetime (SQLite ISO format), use date()/strftime()
    functions for date filtering, not string comparison against a literal
  - the core table for almost any question about suspicious, unusual, or
    flagged activity, large/threshold-breaching amounts, structuring,
    smurfing, or velocity — this table (often joined with counterparties)
    is virtually always needed alongside those topics
""".strip(),
    "alerts": """
alerts(alert_id, transaction_id, rule_triggered, severity, created_date, status)
  - pre-existing alerts already raised by the bank's systems (historical,
    separate from any live rules engine run against a query's results)
  - transaction_id references transactions.transaction_id
  - status is one of: open, closed, escalated
""".strip(),
    "sar_filings": """
sar_filings(sar_id, customer_id, filed_date, narrative_summary, status)
  - Suspicious Activity Reports already filed with regulators
  - customer_id references customers.customer_id
""".strip(),
    "sanctions_list": """
sanctions_list(entry_id, name, country, list_source, date_added)
  - reference watchlist of sanctioned individuals/entities;
    counterparties.is_sanctioned is the simplified boolean flag already
    derived from this list
""".strip(),
    "pep_list": """
pep_list(entry_id, name, country, list_source, date_added)
  - reference watchlist of politically exposed persons;
    counterparties.is_pep is the simplified boolean flag already derived
    from this list
""".strip(),
    "customers_kyc": """
customers(customer_id, name, dob, risk_rating, country, onboarding_date,
         kyc_status, kyc_completion_date, kyc_level)
  - KYC (Know Your Customer) fields added to customers table
  - kyc_status is one of: pending, verified, rejected, expired
  - kyc_level is 0 (none), 1 (basic), 2 (standard), 3 (enhanced)
  - useful for: "which customers have incomplete KYC", "show high-risk customers without verified KYC"
""".strip(),
    "kyc_documents": """
kyc_documents(doc_id, customer_id, doc_type, doc_number, issue_date,
             expiry_date, issuing_country, verified_date, verification_status)
  - KYC documents submitted by customers (passport, driver's license, national ID)
  - doc_type is one of: passport, driver_license, national_id, utility_bill
  - verification_status is one of: pending, verified, rejected
  - useful for: "which customers have expired documents", "show unverified KYC documents"
""".strip(),
    "high_risk_countries": """
high_risk_countries(country_id, country, reason, date_added, is_active)
  - dynamic list of high-risk jurisdictions (OFAC sanctions, money laundering concerns, etc.)
  - is_active = 1 means country is currently flagged; 0 means removed/delisted
  - reason explains why the country is flagged
  - useful for: "which counterparties are from high-risk countries", "show all high-risk countries"
""".strip(),
}

SCHEMA_DESCRIPTION = "\n\n".join(TABLE_DESCRIPTIONS.values())

FEW_SHOT_EXAMPLES = """
Example: "last month's transactions over $10,000"
  SELECT * FROM transactions
  WHERE amount > 10000
    AND txn_date >= date('now', 'start of month', '-1 month')
    AND txn_date <  date('now', 'start of month')

Example: "which counterparties are sanctioned"
  SELECT * FROM counterparties WHERE is_sanctioned = 1

Example: "possible structuring" or "smurfing"
  -- multiple transactions just under the $10,000 reporting threshold,
  -- clustered together, on the same account
  SELECT account_id, COUNT(*) AS n, GROUP_CONCAT(transaction_id) AS txn_ids
  FROM transactions
  WHERE amount BETWEEN 9000 AND 9999.99
  GROUP BY account_id
  HAVING COUNT(*) >= 3
"""


def build_system_prompt(schema_text: str) -> str:
    """Builds the NL->SQL system prompt around whatever schema text is
    supplied — either the full SCHEMA_DESCRIPTION or a retrieved subset
    from Phase 5's schema-retrieval node."""
    return f"""You are a SQL generator for a SQLite database used by an \
anti-money-laundering (AML) analyst. Given a question in plain English, \
output ONE SQLite SELECT statement that answers it.

Database schema:
{schema_text}

{FEW_SHOT_EXAMPLES}

Rules:
- Output ONLY the raw SQL. No markdown code fences, no explanation, no \
trailing commentary.
- Only ever generate a SELECT statement. Never INSERT/UPDATE/DELETE/DROP/ALTER.
- Only reference tables and columns shown in the schema above.
- Do not attempt to judge which transactions are "suspicious" yourself — \
that determination belongs to a separate rules engine, not to SQL \
generation. If asked something like "which are suspicious", just return \
the underlying transaction data relevant to the question (e.g. the \
transactions themselves, or counterparties on watchlists) so a later step \
can evaluate them.
- Use SQLite date functions (date('now'), strftime(), etc.) for relative \
dates like "last month" — never hardcode a literal date.
- When a question asks about activity clustered "within" a time window \
(e.g. "within 48 hours", "in a short window"), never approximate this by \
grouping on DATE(txn_date) or calendar day — a 48-hour window can span two \
calendar dates (e.g. 11pm to 3am), and grouping by day will undercount or \
miss real clusters that cross midnight. If SQL alone can't express a true \
rolling time window, return the raw candidate transactions (e.g. filtered \
by amount range) ungrouped and let a downstream step do the windowing.
"""


SYSTEM_PROMPT = build_system_prompt(SCHEMA_DESCRIPTION)
