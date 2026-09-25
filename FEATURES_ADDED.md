# New Features: KYC & Dynamic High-Risk Countries

This document summarizes the new features added to the AML NL→SQL chatbot.

## Feature 1: Customer KYC (Know Your Customer)

### What was added:

#### 1. New columns on `Customer` table:
- `kyc_status` (String) — one of: `pending`, `verified`, `rejected`, `expired`
  - 70% of customers are verified
  - 15% pending review
  - 10% rejected
  - 5% expired (need re-verification)
- `kyc_completion_date` (Date) — when KYC was completed (NULL if not completed)
- `kyc_level` (Integer) — depth of KYC: 0=none, 1=basic, 2=standard, 3=enhanced

#### 2. New table: `KYCDocument`
```
kyc_documents(doc_id, customer_id, doc_type, doc_number, 
              issue_date, expiry_date, issuing_country, 
              verified_date, verification_status)
```
- Tracks individual KYC documents (passport, driver's license, national ID, etc.)
- Each customer typically has 1–2 documents
- Total: **221 documents across 150 customers**
- Document types: passport, driver_license, national_id, utility_bill
- Verification status: pending, verified, rejected

#### 3. New compliance rule: `check_incomplete_kyc()`
- Flags transactions from customers with `kyc_status` in {pending, rejected, expired}
- **Severity: medium** (requires enhanced monitoring)
- Found **859 transactions** from customers with incomplete KYC in the seeded data
- Only runs if the DataFrame includes `customer_kyc_status` column

### How to query KYC data:

```sql
-- Find customers with incomplete KYC
SELECT * FROM customers 
WHERE kyc_status != 'verified'

-- Show transactions from customers with pending KYC
SELECT t.*, cu.kyc_status
FROM transactions t
JOIN accounts a ON t.account_id = a.account_id
JOIN customers cu ON a.customer_id = cu.customer_id
WHERE cu.kyc_status = 'pending'

-- Find expired KYC documents
SELECT * FROM kyc_documents
WHERE expiry_date < date('now') AND verification_status = 'verified'

-- Show customers by KYC level
SELECT cu.customer_id, cu.name, cu.kyc_level, COUNT(kd.doc_id) as doc_count
FROM customers cu
LEFT JOIN kyc_documents kd ON cu.customer_id = kd.customer_id
GROUP BY cu.customer_id
ORDER BY cu.kyc_level DESC
```

---

## Feature 2: Dynamic High-Risk Countries

### What was changed:

#### 1. High-risk countries moved from hardcoded to database table
**Before:**
```python
# In policy_engine.py (hardcoded)
HIGH_RISK_COUNTRIES = {"Nigeria", "Panama", "Cayman Islands"}
```

**After:**
```sql
-- Query the dynamic list
SELECT country FROM high_risk_countries WHERE is_active = 1
```

#### 2. New table: `HighRiskCountry`
```
high_risk_countries(country_id, country, reason, date_added, is_active)
```
- `country` (String, unique) — country name
- `reason` (String) — why it's flagged (e.g., "OFAC sanctions")
- `date_added` (Date) — when it was added to the list
- `is_active` (Boolean) — 1 = currently flagged, 0 = delisted
- Total: **7 countries** in seeded data
  - Nigeria (OFAC sanctions and money laundering concerns)
  - Panama (Financial secrecy hub)
  - Cayman Islands (Offshore financial center)
  - North Korea (OFAC comprehensive sanctions)
  - Iran (OFAC comprehensive sanctions)
  - Syria (OFAC comprehensive sanctions)
  - Venezuela (OFAC sanctions)

#### 3. Policy engine updated to load dynamically
```python
def get_high_risk_countries(db_path="db/aml.db") -> set[str]:
    """Load active high-risk countries from database (dynamic, not hardcoded)."""
    # Reads from high_risk_countries table
    # Falls back to hardcoded list if table doesn't exist
```

- `check_high_risk_jurisdiction()` now calls `get_high_risk_countries()` instead of using a constant
- Found **181 transactions** with high-risk jurisdiction counterparties
- Can now be updated without recompiling code

### How to manage high-risk countries:

```sql
-- View all active high-risk countries
SELECT * FROM high_risk_countries WHERE is_active = 1

-- Delist a country (mark as inactive)
UPDATE high_risk_countries SET is_active = 0 WHERE country = 'Panama'

-- Add a new high-risk country
INSERT INTO high_risk_countries 
  (country, reason, date_added, is_active)
VALUES 
  ('Country Name', 'Reason for flagging', date('now'), 1)

-- View all countries ever flagged (including delisted)
SELECT * FROM high_risk_countries ORDER BY date_added DESC
```

---

## New Findings in Policy Engine

### Total findings increased from ~293 to **1,160**:

| Rule | Count | Severity | Notes |
|------|-------|----------|-------|
| `ctr_threshold` | 12 | high | Single txns > $10k |
| `structuring` | 29 | high | 3+ txns $9k–$9.99k in 48h |
| `velocity_spike` | 18 | medium | Burst relative to baseline |
| `sanctions_match` | 6 | high | Sanctioned counterparty |
| `pep_match` | 55 | medium | PEP counterparty (not sanctioned) |
| `high_risk_jurisdiction` | 181 | low | Counterparty in high-risk country |
| **`incomplete_kyc`** | **859** | medium | Customer KYC not verified |

The new `incomplete_kyc` rule significantly increases signal, as ~74% of seeded transactions come from customers with pending, rejected, or expired KYC.

---

## Code Changes Summary

### Files Modified:

1. **`db/models.py`**
   - Added `kyc_status`, `kyc_completion_date`, `kyc_level` columns to `Customer`
   - Added new `HighRiskCountry` table
   - Added new `KYCDocument` table

2. **`db/seed.py`**
   - Added `create_high_risk_countries()` function
   - Added `add_kyc_to_customers()` function
   - Added `create_kyc_documents()` function
   - Updated `main()` to call new functions and persist data
   - Imported new table classes

3. **`app/schema.py`**
   - Added schema descriptions for `customers_kyc`, `kyc_documents`, `high_risk_countries`
   - Claude now knows about these tables for NL→SQL generation

4. **`app/policy_engine.py`**
   - Replaced hardcoded `HIGH_RISK_COUNTRIES` with `get_high_risk_countries()` function
   - Added new rule: `check_incomplete_kyc()`
   - Added `check_incomplete_kyc` to `ALL_RULES` list
   - Updated `load_transactions_df()` to include `customer_kyc_status` column
   - Updated SQL join to include customers table

### Backwards Compatibility:

- If `high_risk_countries` table doesn't exist, falls back to hardcoded list
- `check_incomplete_kyc()` only runs if `customer_kyc_status` column is present
- Existing queries still work; new tables are optional

---

## Testing

### Build database with new features:
```bash
rm -f db/aml.db
python -m db.seed
```

### Test policy engine:
```bash
python -m app.policy_engine
# Output: 1,160 findings across 7 rules
```

### Try queries in chatbot:

```bash
# Interactive CLI
python -m app.chat

# Web UI
streamlit run app/streamlit_app.py
```

Try these questions:
- "Which customers have pending KYC?"
- "Show me transactions from customers with incomplete KYC"
- "Which countries are flagged as high-risk?"
- "Show me all high-risk countries and why they're flagged"
- "Find transactions involving counterparties from Iran or Syria"

### Verify new data:

```bash
python3 << 'EOF'
import sqlite3

conn = sqlite3.connect('db/aml.db')
cur = conn.cursor()

print("=== KYC Status Distribution ===")
cur.execute("""
  SELECT kyc_status, COUNT(*) FROM customers GROUP BY kyc_status
""")
for status, count in cur.fetchall():
    print(f"  {status}: {count}")

print("\n=== High-Risk Countries ===")
cur.execute("SELECT country, reason FROM high_risk_countries WHERE is_active = 1")
for country, reason in cur.fetchall():
    print(f"  {country}: {reason}")

print("\n=== KYC Document Types ===")
cur.execute("SELECT doc_type, COUNT(*) FROM kyc_documents GROUP BY doc_type")
for doc_type, count in cur.fetchall():
    print(f"  {doc_type}: {count}")

conn.close()
EOF
```

---

## Next Steps / Future Enhancements

1. **KYC Expiry Rule:** Add a rule that flags transactions from customers with expired KYC
2. **Enhanced KYC Levels:** Extend `check_incomplete_kyc()` to apply different severity based on transaction amount and KYC level
3. **Document Verification:** Flag transactions with unverified KYC documents
4. **Country Delisting:** Implement automatic delisting after a period
5. **Audit Trail:** Track who added/modified high-risk countries and when
6. **UI for Management:** Add Streamlit interface to manage high-risk countries and approve KYC

---

## Summary

✅ Added KYC fields to Customer table  
✅ Added KYCDocument table to track individual documents  
✅ Added HighRiskCountry table for dynamic management  
✅ Implemented `check_incomplete_kyc()` compliance rule  
✅ Made high-risk countries dynamic (database-driven)  
✅ Seeded with realistic KYC distributions and 7 high-risk countries  
✅ Increased policy engine findings from ~293 to 1,160  
✅ All changes are backwards compatible  
✅ Updated schema descriptions for NL→SQL generation
