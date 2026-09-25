# How to Extend KYC & High-Risk Countries Features

This guide shows exactly where and how to add new functionality related to KYC and high-risk countries.

---

## Adding a New Compliance Rule (KYC-Related)

### Example: Flag transactions from customers with expired KYC

#### Step 1: Add the rule function to `app/policy_engine.py`

```python
def check_expired_kyc(df: pd.DataFrame) -> list[Finding]:
    """Flags transactions from customers with expired KYC.
    Only runs if the DataFrame includes customer_kyc_status column."""
    if "customer_kyc_status" not in df.columns:
        return []
    
    expired_kyc = df[df["customer_kyc_status"] == "expired"]
    return [
        Finding(
            transaction_id=int(row.transaction_id),
            rule_triggered="expired_kyc",
            severity="high",  # High severity because KYC needs immediate renewal
            explanation=(
                f"Transaction from customer with EXPIRED KYC status. "
                "Immediate re-verification required. Transaction should be blocked."
            ),
        )
        for row in expired_kyc.itertuples()
    ]
```

#### Step 2: Register the rule in `ALL_RULES`

```python
ALL_RULES = [
    check_ctr_threshold,
    check_structuring,
    check_velocity,
    check_sanctions_and_pep,
    check_high_risk_jurisdiction,
    check_incomplete_kyc,
    check_expired_kyc,  # ← NEW RULE
]
```

#### Step 3: Test it

```bash
python -m app.policy_engine
# Should see output:
#   expired_kyc: 5  (or however many expired customers)
```

#### Step 4: Update documentation

Add to `FEATURES_ADDED.md`:
```markdown
### New Rule: `check_expired_kyc()`
- Flags transactions from customers with expired KYC
- **Severity: High** (transaction should be blocked)
- Found **5 transactions** in seeded data
```

---

## Adding a New KYC Field to Customers

### Example: Add `kyc_approval_date` and `approving_officer`

#### Step 1: Update the database schema in `db/models.py`

```python
class Customer(Base):
    __tablename__ = "customers"
    
    # ... existing fields ...
    kyc_completion_date: Mapped[date | None] = mapped_column(default=None)
    kyc_level: Mapped[int] = mapped_column(default=0)
    
    # ADD THESE NEW FIELDS:
    kyc_approval_date: Mapped[date | None] = mapped_column(default=None)
    approving_officer: Mapped[str | None] = mapped_column(String(100), default=None)
```

#### Step 2: Update the seed generator in `db/seed.py`

```python
def add_kyc_to_customers(customers: list[Customer]) -> None:
    """Add KYC status to customers in-place."""
    kyc_statuses = ["verified", "pending", "rejected", "expired"]
    kyc_weights = [0.7, 0.15, 0.10, 0.05]
    
    officer_names = ["Alice Smith", "Bob Johnson", "Carol Davis", "David Lee"]
    
    for customer in customers:
        customer.kyc_status = random.choices(kyc_statuses, weights=kyc_weights)[0]
        if customer.kyc_status == "verified":
            customer.kyc_completion_date = fake.date_between(
                start_date=customer.onboarding_date, end_date="-30d"
            )
            customer.kyc_level = random.choice([2, 3])
            # ADD THESE:
            customer.kyc_approval_date = customer.kyc_completion_date + timedelta(days=random.randint(1, 7))
            customer.approving_officer = random.choice(officer_names)
        elif customer.kyc_status == "expired":
            # ... existing code ...
            # ADD THESE (same approver, older date):
            customer.approving_officer = random.choice(officer_names)
        else:
            customer.kyc_level = random.choice([0, 1])
```

#### Step 3: Rebuild the database

```bash
rm -f db/aml.db && python -m db.seed
```

#### Step 4: Update the schema description in `app/schema.py`

```python
"customers_kyc": """
customers(customer_id, name, dob, risk_rating, country, onboarding_date,
         kyc_status, kyc_completion_date, kyc_level, kyc_approval_date, approving_officer)
  - kyc_approval_date: when the KYC was approved by an officer
  - approving_officer: name of the compliance officer who approved
  - useful for: "which KYC approvals were done by Alice Smith", "oldest approved KYC records"
""".strip(),
```

#### Step 5: Test it

```bash
python -c "
import sqlite3
conn = sqlite3.connect('db/aml.db')
cur = conn.cursor()
cur.execute('SELECT customer_id, kyc_status, approving_officer FROM customers LIMIT 5')
print(cur.fetchall())
"
```

---

## Managing High-Risk Countries Programmatically

### Example: Add multiple countries at once via Python

#### Option 1: Direct SQL Insert

```python
import sqlite3
from datetime import date

conn = sqlite3.connect('db/aml.db')
cur = conn.cursor()

countries_to_add = [
    ("North Korea", "OFAC comprehensive sanctions", date.today(), 1),
    ("Iran", "OFAC sanctions targeting nuclear program", date.today(), 1),
    ("Syria", "OFAC terrorism-related sanctions", date.today(), 1),
]

for country, reason, date_added, is_active in countries_to_add:
    cur.execute(
        "INSERT OR IGNORE INTO high_risk_countries (country, reason, date_added, is_active) VALUES (?, ?, ?, ?)",
        (country, reason, date_added, is_active)
    )

conn.commit()
conn.close()
```

#### Option 2: Modify the seed generator

Add a `create_high_risk_countries()` parameter:

```python
def create_high_risk_countries(include_custom: list[tuple] = None) -> list[HighRiskCountry]:
    """Create a dynamic high-risk countries list.
    
    Args:
        include_custom: List of (country, reason) tuples to add to the default list
    """
    countries_data = [
        ("Nigeria", "OFAC sanctions and money laundering concerns"),
        # ... existing countries ...
    ]
    
    if include_custom:
        countries_data.extend(include_custom)
    
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
```

Then in `main()`:

```python
high_risk_countries = create_high_risk_countries(
    include_custom=[
        ("Belarus", "EU sanctions on regime"),
        ("Russia", "Sanctions following Ukraine invasion"),
    ]
)
```

---

## Adding New KYC Document Types

### Example: Add "utility_bill" and "bank_statement" types

#### Step 1: Update seed generator in `db/seed.py`

```python
def create_kyc_documents(customers: list[Customer]) -> list[KYCDocument]:
    """Create KYC documents for customers."""
    docs = []
    doc_id = 1
    # ADD "utility_bill" and "bank_statement":
    doc_types = [
        "passport", "driver_license", "national_id", 
        "utility_bill", "bank_statement"  # ← NEW
    ]
    
    for customer in customers:
        # ... existing logic ...
```

#### Step 2: Create a management function in `app/policy_engine.py`

```python
def get_kyc_document_stats(db_path: str = "db/aml.db") -> dict:
    """Get statistics on KYC documents by type."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    stats = {}
    cur.execute("SELECT doc_type, COUNT(*) FROM kyc_documents GROUP BY doc_type")
    for doc_type, count in cur.fetchall():
        stats[doc_type] = count
    
    conn.close()
    return stats
```

#### Step 3: Add a CLI tool to view document stats

Add to `app/policy_engine.py` `main()`:

```python
def main():
    # ... existing code ...
    
    print("\nKYC Document Stats:")
    doc_stats = get_kyc_document_stats()
    for doc_type, count in sorted(doc_stats.items()):
        print(f"  {doc_type}: {count}")
```

---

## Extending the KYC Level System

### Example: Create level-specific transaction limits

#### Step 1: Add a new rule based on KYC level

```python
def check_kyc_level_threshold(df: pd.DataFrame) -> list[Finding]:
    """Flags transactions that exceed thresholds for the customer's KYC level.
    Requires customer_kyc_level column (optional enhancement check).
    """
    if "customer_kyc_level" not in df.columns:
        return []
    
    # Define thresholds by KYC level
    thresholds = {
        0: 0,        # No KYC: block all transactions
        1: 5000,     # Basic KYC: max $5k per transaction
        2: 50000,    # Standard KYC: max $50k per transaction
        3: 500000,   # Enhanced KYC: max $500k per transaction
    }
    
    findings = []
    for row in df.itertuples():
        level = int(row.customer_kyc_level)
        threshold = thresholds.get(level, 0)
        
        if row.amount > threshold:
            findings.append(
                Finding(
                    transaction_id=int(row.transaction_id),
                    rule_triggered="kyc_level_threshold",
                    severity="high" if level < 2 else "medium",
                    explanation=(
                        f"Transaction amount ${row.amount:,.2f} exceeds limit for "
                        f"KYC level {level} (threshold: ${threshold:,.2f})"
                    ),
                )
            )
    
    return findings
```

#### Step 2: Update `load_transactions_df()` to include kyc_level

```python
def load_transactions_df(db_path: str = "db/aml.db", transaction_ids: list[int] | None = None) -> pd.DataFrame:
    """Loads transactions with customer KYC info."""
    conn = sqlite3.connect(db_path)
    query = """
        SELECT
            t.transaction_id, t.account_id, t.amount, t.txn_date,
            c.name AS counterparty_name, c.country AS counterparty_country,
            c.is_pep AS counterparty_is_pep, c.is_sanctioned AS counterparty_is_sanctioned,
            cu.kyc_status AS customer_kyc_status,
            cu.kyc_level AS customer_kyc_level  # ← ADD THIS
        FROM transactions t
        JOIN counterparties c ON t.counterparty_id = c.counterparty_id
        JOIN accounts a ON t.account_id = a.account_id
        JOIN customers cu ON a.customer_id = cu.customer_id
    """
    # ... rest of function
```

#### Step 3: Register the new rule

```python
ALL_RULES = [
    check_ctr_threshold,
    check_structuring,
    check_velocity,
    check_sanctions_and_pep,
    check_high_risk_jurisdiction,
    check_incomplete_kyc,
    check_kyc_level_threshold,  # ← NEW
]
```

---

## Creating a Management API

### Example: REST API to manage high-risk countries

Create `app/kyc_api.py`:

```python
import sqlite3
from flask import Flask, jsonify, request
from datetime import date

app = Flask(__name__)

@app.route('/api/high-risk-countries', methods=['GET'])
def get_countries():
    """List all high-risk countries."""
    conn = sqlite3.connect('db/aml.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM high_risk_countries WHERE is_active = 1")
    countries = [dict(row) for row in cur.fetchall()]
    conn.close()
    return jsonify(countries)

@app.route('/api/high-risk-countries', methods=['POST'])
def add_country():
    """Add a new high-risk country."""
    data = request.json
    conn = sqlite3.connect('db/aml.db')
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO high_risk_countries (country, reason, date_added, is_active) VALUES (?, ?, ?, ?)",
        (data['country'], data['reason'], date.today().isoformat(), 1)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "added"}), 201

@app.route('/api/high-risk-countries/<country>', methods=['DELETE'])
def remove_country(country):
    """Delist a high-risk country."""
    conn = sqlite3.connect('db/aml.db')
    cur = conn.cursor()
    cur.execute("UPDATE high_risk_countries SET is_active = 0 WHERE country = ?", (country,))
    conn.commit()
    conn.close()
    return jsonify({"status": "delisted"}), 200

if __name__ == '__main__':
    app.run(debug=True)
```

Run with:
```bash
python app/kyc_api.py
# Then:
curl http://localhost:5000/api/high-risk-countries
```

---

## Summary: Where to Make Changes

| Feature | File | Function/Section |
|---------|------|------------------|
| Add compliance rule | `app/policy_engine.py` | `def check_X()`, then add to `ALL_RULES` |
| Add KYC field | `db/models.py` | `class Customer` |
| Add document type | `db/seed.py` | `doc_types = [...]` in `create_kyc_documents()` |
| Add high-risk country | `db/seed.py` | `countries_data = [...]` in `create_high_risk_countries()` |
| Update schema for NL→SQL | `app/schema.py` | `TABLE_DESCRIPTIONS` dict |
| Reload transactions for new fields | `app/policy_engine.py` | `load_transactions_df()` SQL query |
| Build management UI | `app/streamlit_app.py` | Add new Streamlit pages/widgets |
| Build REST API | `app/kyc_api.py` | Create new file with Flask routes |

---

## Testing Checklist

After making changes:

```bash
# 1. Rebuild database
rm -f db/aml.db && python -m db.seed

# 2. Run policy engine to see new findings
python -m app.policy_engine

# 3. Test with CLI
python -m app.graph "your question"

# 4. Test with interactive chat
python -m app.chat

# 5. (Optional) Test with web UI
streamlit run app/streamlit_app.py
```

---

## Common Pitfalls

### Pitfall 1: Forgetting to add new fields to `load_transactions_df()` SQL query
**Solution:** Any new column needed by a rule must be added to the JOIN query

### Pitfall 2: Not registering new rule in `ALL_RULES`
**Solution:** Add `check_my_rule` to the `ALL_RULES` list, or it won't run

### Pitfall 3: Forgetting to check if optional column exists
**Solution:** Use `if "column_name" not in df.columns: return []` for optional fields

### Pitfall 4: Breaking schema descriptions for NL→SQL
**Solution:** Add descriptions to `TABLE_DESCRIPTIONS` in `app/schema.py` with examples

### Pitfall 5: Rebuilding database without clearing old one
**Solution:** Always `rm -f db/aml.db` before `python -m db.seed`

