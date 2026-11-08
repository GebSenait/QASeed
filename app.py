"""QASeed local screen. Scenario generation is not implemented."""

import streamlit as st

from qaseed import __version__
from qaseed.config import load_local_env
from qaseed.schema import create_connection, inspect_schema

load_local_env()

st.set_page_config(page_title="QASeed", layout="centered")

st.title("QASeed")
st.caption(f"Developer-first AI test data agent · v{__version__}")

st.markdown(
    """
During regression testing, one fake customer is easy. A customer with a canceled
subscription, an overdue invoice, and a failed payment — all linked — is not.
QASeed will turn a sample schema and a plain-English scenario into synthetic
records that stay consistent across related tables.
"""
)

st.warning("Scenario generation is not yet implemented.")

connection = create_connection()
try:
    schema = inspect_schema(connection)
finally:
    connection.close()

enforcement = "on" if schema["foreign_keys_enabled"] else "off"
st.subheader("Sample schema")
st.caption(
    "Synthetic SaaS tables in an in-memory database. "
    f"Foreign-key enforcement is {enforcement}. "
    "customers → subscriptions → invoices → payments."
)

for table in schema["tables"]:
    st.markdown(f"**{table['name']}**")
    st.table(
        [
            {
                "column": column["name"],
                "type": column["type"],
                "not null": "yes" if column["not_null"] else "",
                "primary key": "yes" if column["primary_key"] else "",
            }
            for column in table["columns"]
        ]
    )
    if table["foreign_keys"]:
        for fk in table["foreign_keys"]:
            st.text(f"{fk['from_column']} → {fk['to_table']}.{fk['to_column']}")
    else:
        st.text("No foreign keys")

st.info("Validation, repair, and LLM planning are planned for later builds.")
