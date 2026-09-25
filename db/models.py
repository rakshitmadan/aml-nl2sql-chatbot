"""SQLAlchemy table definitions for the synthetic AML database."""

from datetime import date, datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    dob: Mapped[date]
    risk_rating: Mapped[str] = mapped_column(String(10))  # low | medium | high
    country: Mapped[str] = mapped_column(String(56))
    onboarding_date: Mapped[date]
    kyc_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | verified | rejected | expired
    kyc_completion_date: Mapped[date | None] = mapped_column(default=None)
    kyc_level: Mapped[int] = mapped_column(default=0)  # 0=none, 1=basic, 2=standard, 3=enhanced


class Account(Base):
    __tablename__ = "accounts"

    account_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"))
    account_type: Mapped[str] = mapped_column(String(20))  # checking | savings | business
    open_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10))  # active | closed | frozen


class Counterparty(Base):
    __tablename__ = "counterparties"

    counterparty_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(56))
    is_pep: Mapped[bool] = mapped_column(default=False)
    is_sanctioned: Mapped[bool] = mapped_column(default=False)


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.account_id"))
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.counterparty_id"))
    amount: Mapped[float]
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    txn_date: Mapped[datetime]
    txn_type: Mapped[str] = mapped_column(String(20))  # wire | ach | cash | check | card
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound


class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.transaction_id"))
    rule_triggered: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))  # low | medium | high
    created_date: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed | escalated


class SarFiling(Base):
    __tablename__ = "sar_filings"

    sar_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"))
    filed_date: Mapped[date]
    narrative_summary: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="filed")  # filed | under_review | closed


class SanctionsListEntry(Base):
    __tablename__ = "sanctions_list"

    entry_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(56))
    list_source: Mapped[str] = mapped_column(String(50))  # e.g. OFAC, UN, EU
    date_added: Mapped[date]


class PepListEntry(Base):
    __tablename__ = "pep_list"

    entry_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(56))
    list_source: Mapped[str] = mapped_column(String(50))
    date_added: Mapped[date]


class HighRiskCountry(Base):
    __tablename__ = "high_risk_countries"

    country_id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(56), unique=True)
    reason: Mapped[str] = mapped_column(String(200))  # e.g., "OFAC sanctions", "Money laundering concerns"
    date_added: Mapped[date]
    is_active: Mapped[bool] = mapped_column(default=True)


class KYCDocument(Base):
    __tablename__ = "kyc_documents"

    doc_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.customer_id"))
    doc_type: Mapped[str] = mapped_column(String(50))  # e.g., passport, driver_license, national_id
    doc_number: Mapped[str] = mapped_column(String(100))
    issue_date: Mapped[date]
    expiry_date: Mapped[date | None] = mapped_column(default=None)
    issuing_country: Mapped[str] = mapped_column(String(56))
    verified_date: Mapped[date | None] = mapped_column(default=None)
    verification_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | verified | rejected
