"""Streamlit smoke test (app.py), run headless with Streamlit's AppTest."""

from streamlit.testing.v1 import AppTest

from qaseed.config import ROOT


def test_app_renders_schema_without_errors():
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert len(app.exception) == 0
    assert app.title[0].value == "QASeed"
    assert len(app.table) == 4  # customers, subscriptions, invoices, payments
    assert any("not yet implemented" in w.value for w in app.warning)
