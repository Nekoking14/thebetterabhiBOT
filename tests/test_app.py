"""Daily workspace flows through Streamlit's supported AppTest."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from config import PROJECT_DIR, ProviderSettings
from repositories import ScrubRepository, local_today
from scrub_service import create_scrub_service


def start_app():
    return AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=20).run()


def search(app, industry="Manufacturing", limit=20, title=""):
    app.radio(key="page").set_value("Account Discovery").run()
    app.selectbox(key="country").set_value("United Kingdom")
    app.selectbox(key="industry").set_value(industry)
    app.number_input(key="min_employees").set_value(500)
    app.number_input(key="max_employees").set_value(5000)
    app.number_input(key="account_limit").set_value(limit)
    app.text_input(key="titles").set_value(title)
    next(b for b in app.button if b.label == "Find Accounts").click().run()
    assert not app.exception
    return app.session_state["search_summary"]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("No external requests")):
        at = start_app()
        assert not at.exception
        yield at


def review_first(app):
    repo = ScrubRepository()
    session = repo.ensure_session()["session_id"]
    account = repo.accounts(session)[0]
    app.selectbox(key=f"review_{session}").set_value(account["id"]).run()
    assert not app.exception
    return account


def test_start_empty_and_daily_session_is_automatic(app):
    assert app.radio(key="page").value == "Today's Scrub List"
    assert next(m for m in app.metric if m.label == "Accounts").value == "0"
    assert len(ScrubRepository().sessions()) == 1
    assert not app.dataframe
    assert not any("Salesforce" in s.label for s in app.selectbox)


def test_three_searches_restart_and_history(app):
    assert search(app)["added"] == 9
    assert search(app, "Logistics")["added"] == 4
    assert search(app) == {"added": 0, "duplicates": 9, "candidates": 9,
                           "below_threshold": 0, "limited": 0, "warnings": []}
    assert next(m for m in app.metric if m.label == "Accounts").value == "13"
    assert len(app.dataframe) == 1 and len(app.dataframe[0].value) == 13
    fresh = start_app()  # New session state, same durable database.
    assert not fresh.exception
    assert next(m for m in fresh.metric if m.label == "Accounts").value == "13"
    repo = ScrubRepository()
    yesterday = local_today() - timedelta(days=1)
    service = create_scrub_service(repo, ProviderSettings())
    service.find_accounts(day=yesterday, limit=2)
    fresh.radio(key="page").set_value("History").run()
    assert not fresh.exception
    assert "Previous Scrub" in [s.value for s in fresh.subheader]
    assert next(m for m in fresh.metric if m.label == "Accounts").value == "2"
    assert len(repo.accounts(repo.ensure_session()["session_id"])) == 13


def test_review_status_persists_immediately(app):
    search(app, limit=1)
    account = review_first(app)
    app.selectbox(key=f"status_{account['id']}").set_value("REVIEWED").run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Reviewed").value == "1"
    fresh = start_app()
    assert next(m for m in fresh.metric if m.label == "Reviewed").value == "1"
    assert fresh.dataframe[0].value.iloc[0]["Status"] == "REVIEWED"


def test_no_people_keeps_account_and_find_better(app):
    search(app, limit=1, title="Astronaut")
    account = review_first(app)
    assert any("No matching prospects saved" in i.value for i in app.info)
    assert any("Account Score: 100 / 100" in m.value for m in app.markdown)
    app.button(key=f"find_{account['id']}").click().run()
    assert not app.exception
    assert any("Prospect Score: 96 / 100" in c.value for c in app.caption)
    assert any("Account Score: 100 / 100" in m.value for m in app.markdown)
    assert len(app.get("link_button")) == 3
    assert ScrubRepository().accounts(account["session_id"])[0]["best_prospect_score"] == 96


def test_custom_persona_validation_before_search(app):
    app.radio(key="page").set_value("Account Discovery").run()
    app.selectbox(key="persona").set_value("Custom")
    next(b for b in app.button if b.label == "Find Accounts").click().run()
    assert not app.exception
    assert any("requires at least one job title" in e.value for e in app.error)
    assert not ScrubRepository().accounts(ScrubRepository().ensure_session()["session_id"])


def test_status_filter(app):
    search(app, limit=2)
    account = review_first(app)
    app.selectbox(key=f"status_{account['id']}").set_value("REJECTED").run()
    app.selectbox(key=f"list_status_{account['session_id']}").set_value("NEW").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 1
    assert next(m for m in app.metric if m.label == "Accounts").value == "2"


def test_app_live_mode_without_key_can_switch_to_mock(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("No external requests")):
        at = start_app()
        assert not at.exception
        assert any("LeadIQ not configured" in w.value for w in at.warning)
        assert not at.error
        next(b for b in at.button if b.label == "Use mock LeadIQ").click().run()
        assert not at.exception
        assert at.selectbox(key="account_source").value == "mock"


def test_table_selection_maps_to_review(app):
    search(app, limit=2)
    session = ScrubRepository().ensure_session()["session_id"]
    # AppTest does not yet provide native dataframe row clicks; exercise the same
    # callback with the selection event shape delivered by the real widget.
    from app import select_table_row
    ids = [a["id"] for a in ScrubRepository().accounts(session)]
    state = {f"scrub_table_{session}": {"selection": {"rows": [1]}}}
    with patch("app.st.session_state", state):
        select_table_row(f"scrub_table_{session}", f"review_{session}", ids)
    assert state[f"review_{session}"] == ids[1]
