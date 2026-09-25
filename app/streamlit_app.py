"""Phase 7: minimal chat UI on top of the LangGraph pipeline built in
Phases 1-6. Multi-turn (one thread_id per browser session, via the
checkpointer from Phase 6), with the generated SQL and policy findings
shown alongside each answer for transparency — an analyst using a
compliance tool should be able to see exactly what query and rules produced
a given answer, not just trust prose.

Run with: ./venv/bin/streamlit run app/streamlit_app.py
"""

import re
import uuid

import streamlit as st

from app.graph import build_graph
from app.analytics import (
    should_show_analytics,
    calculate_transaction_analytics,
    create_transaction_summary,
    create_volume_chart,
    create_debit_credit_chart,
    create_hourly_distribution_chart,
    create_top_counterparties_chart,
    create_day_of_week_chart,
)

st.set_page_config(page_title="AML Analyst Chatbot", page_icon="🔎", layout="centered")


@st.cache_resource
def get_app():
    return build_graph(use_checkpointer=True)


if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "display_messages" not in st.session_state:
    st.session_state.display_messages = []

st.title("🔎 AML Analyst Chatbot")
st.caption(
    "Ask about transactions, counterparties, alerts, or SAR filings in plain English. "
    "Every finding below comes from a deterministic rules engine, not the LLM's judgment."
)

with st.sidebar:
    st.subheader("Session")
    st.text(f"thread_id: {st.session_state.thread_id[:8]}...")
    if st.button("New conversation"):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.display_messages = []
        st.rerun()

    st.subheader("Try asking")
    st.markdown(
        "- Show me last month's transactions and tell me which are suspicious\n"
        "- Which counterparties are sanctioned?\n"
        "- Which accounts show signs of structuring or smurfing?\n"
        "- Which of those accounts had the most transactions? *(follow-up)*"
    )

for msg in st.session_state.display_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("meta"):
            with st.expander("Query details"):
                st.markdown(msg["meta"])

question = st.chat_input("Ask a question about the AML database...")

if question:
    st.session_state.display_messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving schema, generating SQL, running policy checks..."):
            app = get_app()
            config = {"configurable": {"thread_id": st.session_state.thread_id}}
            result = app.invoke(
                {"user_query": question, "retry_count": 0, "validation_errors": []}, config
            )

        if result.get("validation_errors") and not result.get("safe_sql") and not result.get("final_answer"):
            answer = "Sorry, I couldn't generate a valid query for that:\n" + "\n".join(
                f"- {e}" for e in result["validation_errors"]
            )
            meta = None
            analytics_shown = False
        else:
            answer = result["final_answer"]
            retrieved_tables = re.findall(r"^(\w+)\(", result.get("retrieved_schema", ""), re.MULTILINE)
            sql = result.get("safe_sql") or result.get("generated_sql", "")
            findings_count = len(result.get("policy_findings", []))
            rows_count = len(result.get("rows", []))
            policy_engine_note = result.get("policy_engine_note")
            meta = (
                f"**Retrieved tables:** {retrieved_tables}\n\n"
                f"**SQL:**\n```sql\n{sql}\n```\n\n"
                f"**Rows returned:** {rows_count} | **Policy findings:** {findings_count}"
            )
            if policy_engine_note:
                meta += f"\n\n⚠️ **Rules engine did not run:** {policy_engine_note}"

            # Check if user asked for analytics/summary
            analytics_shown = False
            if should_show_analytics(question) and result.get("rows"):
                try:
                    analytics = calculate_transaction_analytics(result["rows"], result["columns"])
                    if "error" not in analytics:
                        analytics_shown = True
                except Exception as e:
                    st.warning(f"Could not calculate analytics: {str(e)}")

        st.markdown(answer)

        # Show analytics and charts if requested and available
        if analytics_shown:
            st.markdown("---")
            st.markdown("### 📊 Transaction Analytics")
            st.markdown(create_transaction_summary(analytics))

            st.markdown("#### Charts & Visualizations")

            col1, col2 = st.columns(2)
            with col1:
                vol_chart = create_volume_chart(analytics)
                if vol_chart:
                    st.plotly_chart(vol_chart, use_container_width=True)

            with col2:
                dc_chart = create_debit_credit_chart(analytics)
                if dc_chart:
                    st.plotly_chart(dc_chart, use_container_width=True)

            col3, col4 = st.columns(2)
            with col3:
                hourly_chart = create_hourly_distribution_chart(analytics)
                if hourly_chart:
                    st.plotly_chart(hourly_chart, use_container_width=True)

            with col4:
                dow_chart = create_day_of_week_chart(analytics)
                if dow_chart:
                    st.plotly_chart(dow_chart, use_container_width=True)

            cp_chart = create_top_counterparties_chart(analytics)
            if cp_chart:
                st.plotly_chart(cp_chart, use_container_width=True)

        if meta:
            with st.expander("Query details"):
                st.markdown(meta)

    st.session_state.display_messages.append({"role": "assistant", "content": answer, "meta": meta})
