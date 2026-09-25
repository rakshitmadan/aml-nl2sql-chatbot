# Phase 1a — Database Schema

## Files created

- [`db/models.py`](../db/models.py) — table definitions
- [`db/database.py`](../db/database.py) — connects to the actual database file

## SQLAlchemy concepts you need to know

SQLAlchemy is an **ORM** (Object-Relational Mapper) — it lets you write a
Python class and have it *become* a database table, instead of writing raw
`CREATE TABLE` SQL by hand.

```python
class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
```

- **`Base`** — every table class inherits from this. It's how SQLAlchemy
  discovers "these are my tables."
- **`Mapped[int]` / `Mapped[str]`** — a Python type hint that SQLAlchemy reads
  to know the column's type. `Mapped[int]` becomes an `INTEGER` column,
  `Mapped[date]` becomes a `DATE` column, etc.
- **`mapped_column(...)`** — extra column config: `primary_key=True`, a max
  string length, a default value.
- **`ForeignKey("customers.customer_id")`** — this is what creates the actual
  *relationship* between tables. It says "this column's values must point to
  a row that exists in `customers`." That's how `accounts.customer_id` knows
  which customer owns the account.

We're **not** writing raw SQL to define these tables — SQLAlchemy generates
it for us. But it's worth knowing what it generates. Run this to see it:

```bash
./venv/bin/python -c "
from sqlalchemy.schema import CreateTable
from db.models import Customer
from db.database import get_engine
print(CreateTable(Customer.__table__).compile(get_engine()))
"
```

## Entity relationship diagram

```
customers ──1───┬──∞─── accounts ──1───┬──∞─── transactions ──∞───1── counterparties
   │            │                       │                                    │
   │ 1                                  │                                    │ (referenced by)
   ∞                                    ∞                              sanctions_list / pep_list
sar_filings                          alerts
```

Read `1───∞` as "one row on the left can relate to many rows on the right."
One customer → many accounts. One account → many transactions. One
transaction → can trigger many alerts (or zero).

## Why these specific columns

- `risk_rating` on `customers` and `is_pep`/`is_sanctioned` on `counterparties`
  exist because AML rules aren't just "is the amount big" — they're
  *conditional* on who's involved. A $9,000 transfer to a sanctioned entity
  is worse than the same amount to a normal counterparty.
- `direction` (`inbound`/`outbound`) on `transactions` matters because some
  AML patterns only make sense in one direction (e.g. structuring is
  typically about *outbound* cash withdrawals staying under a reporting
  threshold).
- `status` on `alerts` and `sar_filings` exists so the chatbot can later
  answer operational questions like "how many open alerts do we have,"
  which is a very realistic thing a compliance analyst would ask.

## Why SQLite for now

Per the architecture doc, SQLite is the "lighter local demo" option — it's a
single file (`db/aml.db`), needs no server running, and is perfect for
building and testing locally. `db/database.py` builds a connection string
(`sqlite:///path/to/aml.db`). If you ever move to PostgreSQL, the only change
needed is that one connection string — none of `models.py` changes, because
SQLAlchemy speaks both dialects.

## Next: seeding it with realistic fake data

An empty schema doesn't prove anything. Next we write a generator that fills
these tables with a few thousand rows using `Faker`, including a handful of
*deliberately* suspicious patterns (structuring, sanctioned counterparties)
so the chatbot has real things to find later.
