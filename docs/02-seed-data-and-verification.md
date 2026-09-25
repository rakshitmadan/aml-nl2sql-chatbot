# Phase 1b — Seed Data & Verification

## Files created

- [`db/seed.py`](../db/seed.py) — generates the fake data
- [`tests/verify_seed.py`](../tests/verify_seed.py) — proves the patterns exist

## How the generator works

`db/seed.py` builds the database bottom-up, in dependency order (you can't
create an account before its customer exists):

1. `customers` (150) → `accounts` (1-2 each, 227 total) → `counterparties`
   (120, with 7 marked `is_sanctioned` and 9 marked `is_pep`) → matching
   `sanctions_list` / `pep_list` rows.
2. **"Noise" transactions** (2,800) — ordinary, unremarkable activity: small
   amounts ($20-$8,000), spread randomly over the last 6 months. This is the
   realistic bulk of the data — most real transactions aren't suspicious,
   and the chatbot needs to prove it can find the few that are, not just
   report on everything.
3. **Deliberately planted patterns**, layered on top of the noise:
   - **8 structuring sequences** — 3-4 transactions of $9,000-$9,900 each,
     clustered within a 48-hour window on the same account. This mimics
     "smurfing": deliberately staying just under the $10k reporting
     threshold across several transactions instead of one.
   - **10 large transactions** over $10,000 — straightforward CTR
     (Currency Transaction Report) threshold breaches.
   - **6 transactions with sanctioned counterparties** — tests the
     sanctions-screening rule.
   - **1 velocity spike** — one account that's normally quiet suddenly
     makes 18 transactions in 3 days. Unlike the other patterns, this one
     is only suspicious *relative to that account's own baseline* — no
     single transaction looks wrong.
4. `alerts` (40) and `sar_filings` (15) — pre-existing historical records,
   simulating alerts a bank's system already generated in the past. These
   are a **separate concept** from the live AML Policy Engine we'll build in
   Phase 4: `alerts` is "what the bank has already flagged," while Phase 4
   computes fresh findings from whatever the chatbot just queried. Keeping
   them separate mirrors reality — automated rules run on live data, but a
   bank also has a history of past cases.

**Reproducibility**: `random.seed(42)` and `Faker.seed(42)` mean re-running
`seed.py` regenerates the *same* patterns every time — useful so your demo
results don't change between runs. (Transaction dates are relative to
"now," so which calendar month is "last month" does shift with the actual
date — but the shape of the data doesn't.)

## Verifying it actually worked

Generating data isn't the same as generating *correct* data — I wrote
[`tests/verify_seed.py`](../tests/verify_seed.py) to query the database with
plain SQL/pandas and confirm the intended patterns are actually present and
findable. Run it yourself:

```bash
./venv/bin/python -m tests.verify_seed
```

Results (after the counterparty-risk-distribution fix described below —
exact counts shift slightly on each re-seed since a code change shifts the
sequence of random draws, but the shape stays the same):

| Check | Found | Expected |
|---|---|---|
| CTR breaches (> $10,000) | 12 | 10 planted (+2 incidental — a couple of the sanctioned-counterparty transactions also happened to exceed $10k, which is realistic overlap, not a bug) |
| Sanctioned counterparty transactions | 6 | 6 |
| Structuring (3+ txns of $9k-$9,999 on one account within 48h) | 8 accounts | 8 sequences |
| True velocity spike (≥15 txns in any 3-day window) | 1 account | 1 planted |

### Bug 1: naive velocity check counted lifetime totals, not a real window

My first version of the velocity check just did
`GROUP BY account_id HAVING COUNT(*) >= 15` — total transactions *ever* on
an account. That's wrong: with ~2,863 transactions spread across 227
accounts (~12.6 average), ordinary random variance alone pushes some normal
accounts over a flat count of 15, with no actual burst in time. It
flagged a dozen "spikes" that weren't real.

The fix: for each account, sort its transactions by time and slide a
3-day window across them, checking whether *any* window contains 15+
transactions — a real rolling-window check, not a lifetime total. That's
what `find_velocity_spikes()` in `verify_seed.py` does with pandas. It now
correctly finds exactly the one account we planted.

This matters beyond this one script: it's the same mistake a naive "velocity
check" rule could make in the real AML Policy Engine (Phase 4) — worth
remembering when we build that rule for real.

### Bug 2: unrealistic PEP/jurisdiction base rates, caught only once Phase 4 existed

This one didn't show up until Phase 4 (the AML Policy Engine) actually ran
its rules over this data: the high-risk-jurisdiction and PEP-match rules
fired on **790** and **234** transactions respectively, out of 2,863 total —
28% and 8% of *everything*, which is useless as an alerting signal (a rule
that fires on a quarter of all transactions tells an analyst nothing).

Root cause: counterparties' `country` and `is_pep` were originally assigned
*uniformly at random* — 3 of the 10 countries in the generator are
"high-risk," so ~27% of ordinary, unremarkable counterparties ended up in
one purely by chance, unrelated to any actual suspicious behavior. Since
noise transactions picked counterparties uniformly too, that 27% base rate
leaked straight into the transaction data.

Fixed in `db/seed.py` by splitting non-sanctioned counterparties into a
`low_risk` bucket and a smaller `elevated` bucket (PEP and/or high-risk
country), and having ordinary noise transactions pick from `elevated` only
`ELEVATED_COUNTERPARTY_RATE = 0.08` of the time — a realistic low base rate
— while the deliberately-planted structuring/CTR/velocity patterns pull
from `low_risk` exclusively, so those findings stay attributable to one
clear cause instead of being muddied by incidental jurisdiction/PEP flags.
After the fix: jurisdiction hits dropped to ~7% of transactions, PEP hits to
~2% — still real signal, no longer noise.

Worth internalizing as a general lesson: **a rule can be logically correct
and still be useless if the data it runs against doesn't reflect realistic
base rates.** It only surfaced by running the full pipeline end-to-end
(Phase 1 data → Phase 4 rules), not by checking each phase in isolation.

## Phase 1 status: done

We now have a database with realistic structure, realistic noise, and known,
verifiable suspicious patterns. Everything from Phase 2 onward can be tested
against ground truth we already know exists.
