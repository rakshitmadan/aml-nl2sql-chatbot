"""Transaction analytics and visualization helpers."""

import sqlite3
from datetime import datetime
from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px


def should_show_analytics(question: str) -> bool:
    """Detect if question asks for analytics, summary, or charts."""
    keywords = ["summary", "analytics", "chart", "dashboard", "breakdown", "trend", "spike", "spike", "volume"]
    return any(keyword in question.lower() for keyword in keywords)


def calculate_transaction_analytics(rows: list[tuple], columns: list[str]) -> dict:
    """Calculate comprehensive transaction analytics from query results."""
    if not rows:
        return {"error": "No transactions to analyze"}

    # Convert to DataFrame for easier analysis
    df = pd.DataFrame(rows, columns=columns)

    # Basic stats
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        total_amount = df["amount"].sum()
        avg_amount = df["amount"].mean()
        max_amount = df["amount"].max()
        min_amount = df["amount"].min()
    else:
        total_amount = avg_amount = max_amount = min_amount = 0

    # Debit/Credit breakdown
    debits = credits = net = 0
    if "direction" in df.columns:
        debits = df[df["direction"] == "outbound"]["amount"].sum() if "amount" in df.columns else 0
        credits = df[df["direction"] == "inbound"]["amount"].sum() if "amount" in df.columns else 0
        net = credits - debits

    # Transaction count
    txn_count = len(df)

    # Top counterparties
    top_counterparties = {}
    if "counterparty_name" in df.columns:
        top_counterparties = (
            df["counterparty_name"]
            .value_counts()
            .head(5)
            .to_dict()
        )

    # Time analysis (if txn_date available)
    time_stats = {}
    if "txn_date" in df.columns:
        df["txn_date"] = pd.to_datetime(df["txn_date"])
        df["hour"] = df["txn_date"].dt.hour
        df["day_of_week"] = df["txn_date"].dt.day_name()
        df["date"] = df["txn_date"].dt.date

        time_stats = {
            "by_hour": df["hour"].value_counts().to_dict(),
            "by_day_of_week": df["day_of_week"].value_counts().to_dict(),
            "by_date": df["date"].value_counts().to_dict(),
        }

    # Currency breakdown (if currency column exists)
    currency_stats = {}
    if "currency" in df.columns:
        currency_stats = df["currency"].value_counts().to_dict()

    # Transaction type breakdown
    txn_type_stats = {}
    if "txn_type" in df.columns:
        txn_type_stats = df["txn_type"].value_counts().to_dict()

    return {
        "transaction_count": txn_count,
        "total_amount": total_amount,
        "average_amount": avg_amount,
        "max_amount": max_amount,
        "min_amount": min_amount,
        "total_debits": debits,
        "total_credits": credits,
        "net_position": net,
        "top_counterparties": top_counterparties,
        "time_stats": time_stats,
        "currency_stats": currency_stats,
        "txn_type_stats": txn_type_stats,
        "dataframe": df,
    }


def create_transaction_summary(analytics: dict) -> str:
    """Create a formatted transaction summary."""
    if "error" in analytics:
        return analytics["error"]

    summary = f"""
## Transaction Summary

**Total Transactions:** {analytics["transaction_count"]}
**Total Amount:** ${analytics["total_amount"]:,.2f}
**Average Transaction:** ${analytics["average_amount"]:,.2f}
**Range:** ${analytics["min_amount"]:,.2f} - ${analytics["max_amount"]:,.2f}

### Debit/Credit Breakdown
- **Total Credits (Inbound):** ${analytics["total_credits"]:,.2f}
- **Total Debits (Outbound):** ${analytics["total_debits"]:,.2f}
- **Net Position:** ${analytics["net_position"]:,.2f}

### Top Counterparties
"""

    for i, (cp, count) in enumerate(analytics["top_counterparties"].items(), 1):
        summary += f"{i}. {cp}: {count} transactions\n"

    if analytics["currency_stats"]:
        summary += "\n### Currency Distribution\n"
        for currency, count in analytics["currency_stats"].items():
            summary += f"- {currency}: {count} transactions\n"

    if analytics["txn_type_stats"]:
        summary += "\n### Transaction Types\n"
        for txn_type, count in analytics["txn_type_stats"].items():
            summary += f"- {txn_type}: {count} transactions\n"

    return summary


def create_volume_chart(analytics: dict):
    """Create a transaction volume chart (by date)."""
    if "by_date" not in analytics["time_stats"]:
        return None

    df_dates = analytics["dataframe"].copy()
    df_dates["date"] = pd.to_datetime(df_dates["txn_date"]).dt.date
    daily_volume = df_dates.groupby("date").size().reset_index(name="count")

    fig = px.line(
        daily_volume,
        x="date",
        y="count",
        title="Transaction Volume Over Time",
        labels={"date": "Date", "count": "Number of Transactions"},
        markers=True,
    )
    fig.update_layout(hovermode="x unified", height=400)
    return fig


def create_debit_credit_chart(analytics: dict):
    """Create a debit vs credit breakdown chart."""
    data = {
        "Direction": ["Inbound (Credit)", "Outbound (Debit)"],
        "Amount": [analytics["total_credits"], analytics["total_debits"]],
    }
    df_dc = pd.DataFrame(data)

    fig = px.bar(
        df_dc,
        x="Direction",
        y="Amount",
        title="Total Debits vs Credits",
        labels={"Amount": "Amount ($)"},
        color="Direction",
    )
    fig.update_layout(height=400, showlegend=False)
    return fig


def create_hourly_distribution_chart(analytics: dict):
    """Create hourly transaction distribution chart."""
    if "by_hour" not in analytics["time_stats"]:
        return None

    hours = list(range(24))
    counts = [analytics["time_stats"]["by_hour"].get(h, 0) for h in hours]

    fig = go.Figure(data=[go.Bar(x=hours, y=counts)])
    fig.update_layout(
        title="Transactions by Hour of Day",
        xaxis_title="Hour",
        yaxis_title="Count",
        height=400,
    )
    return fig


def create_top_counterparties_chart(analytics: dict):
    """Create a chart of top counterparties by transaction count."""
    if not analytics["top_counterparties"]:
        return None

    counterparties = list(analytics["top_counterparties"].keys())
    counts = list(analytics["top_counterparties"].values())

    fig = px.bar(
        x=counts,
        y=counterparties,
        orientation="h",
        title="Top Counterparties by Transaction Count",
        labels={"x": "Number of Transactions", "y": "Counterparty"},
    )
    fig.update_layout(height=400)
    return fig


def create_day_of_week_chart(analytics: dict):
    """Create transaction volume by day of week."""
    if "by_day_of_week" not in analytics["time_stats"]:
        return None

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow_stats = analytics["time_stats"]["by_day_of_week"]
    counts = [dow_stats.get(day, 0) for day in day_order]

    fig = px.bar(
        x=day_order,
        y=counts,
        title="Transactions by Day of Week",
        labels={"x": "Day", "y": "Count"},
    )
    fig.update_layout(height=400)
    return fig
