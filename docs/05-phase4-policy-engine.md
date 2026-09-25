# Phase 4 — AML Policy Engine (Deterministic Python)

## Files created

- [`app/policy_engine.py`](../app/policy_engine.py) — the five rules
- [`tests/test_policy_engine.py`](../tests/test_policy_engine.py) — checks findings against known ground truth

## Why this file has zero LLM calls in it

This is the whole point of the architecture, and it's worth restating
plainly: **nothing in `policy_engine.py` calls a language model.** Every
finding comes from a `pandas` filter, a `groupby`, or a sliding-window scan
over plain data. Given the same transactions, it produces the exact same
findings every time — no temperature, no prompt sensitivity, no chance of
the model deciding a transaction is suspicious for a reason it can't
justify. That's the property a real compliance system needs, and it's why
this logic is intentionally kept as far from the LLM as possible in the
whole design.

## The five rules

Each rule takes a `pandas.DataFrame` (any DataFrame with the right columns
— see `REQUIRED_COLUMNS`) and returns a list of `Finding` objects
(`transaction_id`, `rule_triggered`, `severity`, `explanation`):

1. **`check_ctr_threshold`** — any single transaction over $10,000. The
   simplest rule: one filter, one line.
2. **`check_structuring`** — transactions of $9,000-$9,999.99, clustered
   within a 48-hour window, 3+ on the same account. Uses a proper
   **sliding-window** algorithm (two-pointer, O(n) per account): sort an
   account's qualifying transactions by time, slide a 48h window across
   them, and merge any overlapping windows that clear the 3-transaction
   threshold into one cluster. I got this right the first time here
   specifically *because* I got it wrong earlier in Phase 1
   (`tests/verify_seed.py`'s first velocity check counted lifetime totals
   instead of a real window) — same mistake, different rule, avoided by
   remembering the earlier one.
3. **`check_velocity`** — same sliding-window technique, but instead of a
   flat count, the threshold is computed **relative to each account's own
   average rate**: `spike_threshold = max(10, 3 × expected_txns_in_a_3day_window_for_this_account)`.
   An account that normally does 20 txns/day isn't spiking at 20 in 3 days;
   an account that normally does 1/day is. This is what "vs. account
   baseline" in the architecture doc actually means in code.
4. **`check_sanctions_and_pep`** — any transaction touching a counterparty
   flagged `is_sanctioned` (severity `high`) or `is_pep` without being
   sanctioned (severity `medium`).
5. **`check_high_risk_jurisdiction`** — counterparty located in a
   designated high-risk country (`Nigeria`, `Panama`, `Cayman Islands` in
   this synthetic dataset — a real system would use an actual FATF-type
   list).

`run_policy_engine(df)` just runs all five and concatenates the results.

## A bug that only showed up once this phase existed

Running the engine over the full seeded database the first time produced
**1,091 findings out of 2,863 transactions** — and two rules were doing
almost all of it: `high_risk_jurisdiction` (790) and `pep_match` (234).
That's 28% and 8% of *every transaction in the database*, which is not a
usable alert — a real analyst looking at a rule that fires on a quarter of
all activity would (correctly) ignore it.

This wasn't a bug in the rule logic — `check_high_risk_jurisdiction` does
exactly what it says. It was a bug in the **Phase 1 data generator**:
counterparty country and PEP status were assigned uniformly at random, so
~27% of ordinary counterparties ended up in a "high-risk" country purely by
chance, with no connection to any actual suspicious pattern. The rule was
faithfully reporting noise as if it were signal.

I went back and fixed `db/seed.py` (documented in the updated
[02-seed-data-and-verification.md](02-seed-data-and-verification.md)) so
ordinary transactions only touch elevated-risk counterparties at a
realistic ~8% base rate, while the deliberately-planted patterns
(structuring, CTR, velocity) route through low-risk counterparties only, so
each stays attributable to one clear cause. Re-ran the whole pipeline after
the fix — Phase 1's own patterns still verify correctly, and the policy
engine's jurisdiction/PEP rates dropped to ~7%/~2%, which is a rate an
analyst could actually act on.

**The general lesson, worth carrying forward**: a rule can be completely
correct in isolation and still be useless if the data it runs against
doesn't reflect realistic base rates. This only became visible by running
the *full* pipeline (seed data → policy engine) rather than testing each
phase in isolation — worth remembering as we wire more pieces together.

## Testing it

`tests/test_policy_engine.py` checks findings against ground truth already
established in Phase 1 (exact expected transaction IDs for CTR and
sanctions, exact expected account counts for structuring and velocity), plus
a regression guard that fails the test suite if jurisdiction/PEP rates ever
creep back up past 15%/10% — so a future change can't silently reintroduce
Bug 2.

```bash
./venv/bin/python -m app.policy_engine          # run + summary over the full DB
./venv/bin/python -m tests.test_policy_engine   # ground-truth checks
```

Current results: 12 CTR breaches, 6 sanctions matches, 28 structuring
transactions across exactly 8 accounts, 18 velocity-spike transactions on
exactly 1 account, ~7% jurisdiction rate, ~2% PEP rate — all matching
Phase 1's planted ground truth exactly where it's supposed to be exact, and
realistic where it's supposed to be realistic.

## What's still not wired up

The policy engine is standalone right now — `load_transactions_df()` pulls
straight from the database for testing, bypassing the LangGraph pipeline
entirely. It isn't yet a node in the Phase 3 graph, and there's no
Response Synthesizer turning its findings into the natural-language answer
from the original goal ("You had 20 transactions... 1 exceeded the $10,000
threshold..."). That wiring — plugging this into `app/graph.py` after the
SQL Executor node, then adding a synthesizer node — is the natural next
step before Phase 5 (schema RAG) or Phase 6 (memory) make much sense to add.

## Phase 4 status: rules done and tested; full pipeline integration still open
