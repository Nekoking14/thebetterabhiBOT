"""Compact local SDR workspace. SQLite owns lists; session state owns UI/cache only."""
import sqlite3
from dataclasses import replace
from datetime import date

import streamlit as st

from config import get_settings, leadiq_is_configured
from leadiq import LeadIQError
from personas import PERSONAS, SKILL_GROUPS
from prospect_discovery import ProspectFilters
from providers import ProviderError
from repositories import STATUSES, ScrubRepository
from sales_navigator import sales_navigator_link
from scrub_service import create_scrub_service

# Centralized, restrained theme. No downloaded assets or provider branding.
STYLE = """
<style>
:root { --navy: #053856; --border: #DFE3E3; }
.stApp { background: #F4F8F8; color: #0D2D44; }
[data-testid="stSidebar"] { background: white; border-right: 1px solid var(--border); }
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
h1 { font-size: 1.6rem !important; color: var(--navy); }
h2, h3 { font-size: 1.15rem !important; color: var(--navy); }
[data-testid="stMetric"] { background: white; border: 1px solid var(--border); padding: .5rem .8rem; border-radius: 4px; }
[data-testid="stMetricValue"] { font-size: 1.4rem; color: var(--navy); }
[data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"] { border-radius: 4px; }
button[kind="primary"], button[kind="primaryFormSubmit"] { background: #04FF88; color: #0D2D44; border: 1px solid #04FF88; }
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover { background: #04E87C; color: #0D2D44; border-color: #053856; }
button:focus-visible { outline: 2px solid #053856; outline-offset: 2px; }
</style>
"""


def display_date(value):
    day = date.fromisoformat(value)
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def use_mock_sources():
    st.session_state["account_source"] = "mock"
    st.session_state["prospect_source"] = "mock"


def filters_form(service, settings):
    previous = st.session_state.get("discovery_defaults", {})
    industries = sorted({c["industry"] for c in service.discovery.account_provider.get_companies()
                         if c.get("industry")}) if settings.account == "mock" else ["Manufacturing", "Technology", "Logistics"]
    with st.form("discovery_filters"):
        st.markdown("**Account filters**")
        columns = st.columns([1.2, 1.4, 1, 1, 1.4])
        countries = ["All", "United Kingdom", "Ireland"]
        country = columns[0].selectbox("Country", countries, index=countries.index(previous.get("country", "All")), key="country")
        industry_options = ["All"] + industries
        if previous.get("industry") and previous["industry"] not in industry_options:
            industry_options.append(previous["industry"])
        industry = columns[1].selectbox("Industry", industry_options,
                                        index=industry_options.index(previous.get("industry", "All")), key="industry",
                                        accept_new_options=settings.account == "leadiq")
        minimum = columns[2].number_input("Minimum employees", min_value=0, value=previous.get("min_employees", 500), key="min_employees")
        maximum = columns[3].number_input("Maximum employees", min_value=0, value=previous.get("max_employees", 5000), key="max_employees")
        account_score = columns[4].slider("Minimum Account Score", 0, 100,
                                          previous.get("account_score", 60 if settings.account == "mock" else 0), key="account_score")
        st.markdown("**Prospect filters**")
        columns = st.columns([1.3, 1.5, 1.4, 1.4, 1])
        personas = ["All"] + list(PERSONAS)
        persona = columns[0].selectbox("Persona", personas, index=personas.index(previous.get("persona", "All")), key="persona")
        titles = columns[1].text_input("Job titles", value=previous.get("titles", ""), key="titles", placeholder="Head of IT, IT Director",
                                       help="Optional comma-separated titles; required for Custom persona. Titles use OR.")
        seniorities = columns[2].multiselect("Seniority", ["C-Level", "VP", "Director", "Head", "Manager", "Senior",
                                                          "Individual Contributor", "Entry"], default=previous.get("seniorities", []), key="seniorities")
        prospect_score = columns[3].slider("Minimum Prospect Score", 0, 100, previous.get("prospect_min", 0), key="prospect_min")
        per_account = columns[4].number_input("Prospects per account", min_value=1, max_value=10, value=previous.get("per_company", 3), key="per_company")
        with st.expander("More filters"):
            columns = st.columns(3)
            limit = columns[0].number_input("Account result limit", min_value=1, max_value=100, value=previous.get("account_limit", 20), key="account_limit")
            functions = columns[1].multiselect("Function", ["Information Technology", "Security", "Marketing", "Sales", "Finance"], default=previous.get("functions", []), key="functions")
            groups = columns[2].multiselect("Relevant skill groups", list(SKILL_GROUPS), default=previous.get("skill_groups", []), key="skill_groups",
                                            help="Ranking evidence only. Missing skills do not exclude people.")
        clicked = st.form_submit_button("Find Accounts", type="primary")
        st.caption("Prospect filters rank people; companies remain eligible even when no person matches.")
    if clicked:
        st.session_state["discovery_defaults"] = {key: st.session_state[key] for key in
            ("country", "industry", "min_employees", "max_employees", "account_score", "persona", "titles",
             "seniorities", "prospect_min", "per_company", "account_limit", "functions", "skill_groups")}
        filters = ProspectFilters(persona=None if persona == "All" else persona,
                                  job_titles=tuple(t.strip() for t in titles.split(",") if t.strip()),
                                  seniorities=tuple(seniorities), functions=tuple(functions),
                                  skill_groups=tuple(groups), min_score=prospect_score, limit=per_account)
        with st.spinner("Finding accounts and ranking prospects…"):
            summary = service.find_accounts(prospect_filters=filters, country=None if country == "All" else country,
                                            industry=None if industry == "All" else industry, min_employees=minimum,
                                            max_employees=maximum, min_score=account_score, limit=limit)
        st.session_state["prospect_filters"] = filters
        st.session_state["search_summary"] = summary
        st.session_state["requested_page"] = "Today's Scrub List"
        st.rerun()


def select_table_row(table_key, review_key, account_ids):
    rows = st.session_state[table_key]["selection"]["rows"]
    if rows and rows[0] < len(account_ids):
        st.session_state[review_key] = account_ids[rows[0]]


def update_status(repository, account_id, key):
    try:
        repository.set_status(account_id, st.session_state[key])
    except (sqlite3.Error, ValueError, OSError) as exc:
        st.session_state["status_error"] = str(exc)
        st.session_state.pop(key, None)


def review_panel(account, repository, service):
    with st.container(border=True):
        st.subheader(account["company_name"])
        columns = st.columns([1, 2])
        with columns[0]:
            st.write(f"Account Score: {account['account_score']} / 100 · {account['account_score_band']}")
            for label, field in (("Domain", "domain"), ("Industry", "industry"), ("Employees", "employee_count"),
                                 ("Country", "country"), ("City", "city")):
                st.caption(f"{label}: {account.get(field) if account.get(field) is not None else 'Unknown'}")
            st.caption(f"Source: {account['source']} · Added {account['date_added'][:10]}")
            key = f"status_{account['id']}"
            st.selectbox("Status", STATUSES, index=STATUSES.index(account["status"]), key=key,
                         on_change=update_status, args=(repository, account["id"], key))
        with columns[1]:
            st.markdown("**Best Prospects**")
            st.caption(f"{account['matching_prospects']} matching people in the last retrieved pool · Prospect source: {account['prospect_source']}")
            if not account["prospects"]:
                st.info("No matching prospects saved. Keep this account and broaden the prospect filters.")
            for rank, prospect in enumerate(account["prospects"][:3], 1):
                score = prospect["prospect_score"]
                st.write(f"{rank}. {prospect['full_name'] or 'Name unavailable'} · {prospect['job_title'] or 'Title unavailable'}")
                st.caption(f"Prospect Score: {score['total_score']} / 100 · {score['score_band']}")
                link = sales_navigator_link(prospect, account["company_name"])
                st.link_button("Open in Sales Navigator", link["url"])
                if link["is_search"]:
                    with st.expander(f"Manual search text · prospect {rank}"):
                        st.caption("If Sales Navigator does not prefill keywords, paste this text manually.")
                        st.code(link["search_text"], language=None)
            if not account["prospects"]:
                st.link_button("Open in Sales Navigator", sales_navigator_link({}, account["company_name"])["url"])
            with st.expander("Refine prospect search"):
                persona = st.selectbox("Review persona", ["All"] + list(PERSONAS), key=f"review_persona_{account['id']}")
                titles = st.text_input("Review job titles", key=f"review_titles_{account['id']}", placeholder="Head of IT, IT Director")
                minimum = st.slider("Review minimum prospect score", 0, 100, 0, key=f"review_min_{account['id']}")
                st.caption("Uses the same company. Blank review filters search all personas; returns the top three.")
            if st.button("Find Better Prospects", key=f"find_{account['id']}"):
                filters = ProspectFilters(persona=None if persona == "All" else persona,
                                          job_titles=tuple(t.strip() for t in titles.split(",") if t.strip()),
                                          min_score=minimum, limit=3)
                with st.spinner("Ranking people in this company…"):
                    service.find_better(account, filters)
                st.session_state["review_message"] = "Prospects updated. Account Score is unchanged."
                st.rerun()


def scrub_table(accounts, session_id, repository, service):
    status_filter = st.selectbox("Show status", ["All"] + list(STATUSES), key=f"list_status_{session_id}")
    visible = [a for a in accounts if status_filter == "All" or a["status"] == status_filter]
    if not accounts:
        st.info("Your scrub list is empty. Open Account Discovery and click Find Accounts.")
        return
    if not visible:
        st.info("No accounts have this status.")
        return
    table = []
    for account in visible:
        best = account["prospects"][0] if account["prospects"] else {}
        table.append({"Score": account["account_score"], "Company": account["company_name"],
                      "Employees": account["employee_count"], "Industry": account["industry"], "Country": account["country"],
                      "Best Prospect": " · ".join(filter(None, [account["best_prospect_name"], account["best_prospect_title"]])) or "None saved",
                      "Prospect Score": account["best_prospect_score"], "Status": account["status"],
                      "Actions": sales_navigator_link(best, account["company_name"])["url"]})
    review_key = f"review_{session_id}"
    ids = [a["id"] for a in visible]
    st.caption("Select a row to Review. Click a column heading to sort, or Open Sales Nav in Actions.")
    table_key = f"scrub_table_{session_id}"
    st.dataframe(table, hide_index=True, width="stretch", height=min(560, 38 + 35 * len(table)),
                 column_config={"Actions": st.column_config.LinkColumn("Actions", display_text="Open Sales Nav"),
                                "Score": st.column_config.NumberColumn(format="%d"),
                                "Prospect Score": st.column_config.NumberColumn(format="%d")},
                 key=table_key, selection_mode="single-row",
                 on_select=lambda: select_table_row(table_key, review_key, ids))
    if st.session_state.get(review_key) not in [None] + ids:
        st.session_state[review_key] = None
    selected = st.selectbox("Review", [None] + ids, key=review_key,
                            format_func=lambda value: "Choose an account to review" if value is None else
                            next(a["company_name"] for a in visible if a["id"] == value))
    if selected is not None:
        review_panel(next(a for a in visible if a["id"] == selected), repository, service)


def main():
    st.set_page_config(page_title="Account Finder", layout="wide")
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        repository = ScrubRepository()
        today = repository.ensure_session()
        configured = get_settings(include_salesforce=False)
        if "requested_page" in st.session_state:
            st.session_state["page"] = st.session_state.pop("requested_page")
        st.session_state.setdefault("page", "Today's Scrub List")
        with st.sidebar:
            st.markdown("**Account Finder**")
            page = st.radio("Workspace", ["Account Discovery", "Today's Scrub List", "History"],
                            key="page", label_visibility="collapsed")
            today_accounts = repository.accounts(today["session_id"])
            st.caption(f"Today · {len(today_accounts)} accounts")
            with st.expander("Data sources"):
                account_source = st.selectbox("Account source", ["mock", "leadiq"],
                                              index=["mock", "leadiq"].index(configured.account), key="account_source")
                prospect_source = st.selectbox("Prospect source", ["mock", "leadiq"],
                                               index=["mock", "leadiq"].index(configured.prospect), key="prospect_source")
                st.caption("Session overrides only. Credentials remain in .env.")
            st.caption("Local mode · Data stored locally on this Mac")
        settings = replace(configured, account=account_source, prospect=prospect_source)
        if (st.session_state.get("provider_settings") != settings or
                st.session_state.get("repository_path") != str(repository.path)):
            st.session_state["provider_settings"] = settings
            st.session_state["repository_path"] = str(repository.path)
            st.session_state["scrub_service"] = create_scrub_service(repository, settings)
            st.session_state.pop("account_score", None)
            st.session_state.get("discovery_defaults", {}).pop("account_score", None)
        service = st.session_state["scrub_service"]
        header = st.columns([3, 1])
        header[0].title("Account Finder")
        header[0].caption("Discover and review today's target accounts")
        header[1].caption(f"Today · {len(today_accounts)} accounts")
        if settings.account == "leadiq" or settings.prospect == "leadiq":
            if not leadiq_is_configured():
                st.warning("LeadIQ not configured. Add your API key to .env or use mock LeadIQ.")
                st.button("Use mock LeadIQ", on_click=use_mock_sources)
            st.caption(f"LeadIQ: {service.discovery.client.status if leadiq_is_configured() else 'Not configured'} · Read-only")
        else:
            st.caption("Mock LeadIQ · Fictional company and prospect data")
        session = today
        if page == "History":
            history = [s for s in repository.sessions() if s["session_date"] < today["session_date"]]
            st.subheader("History")
            if not history:
                st.info("No previous scrub lists yet. Today's list will remain here on the next local calendar day.")
                return
            session_id = st.selectbox("Scrub date", [s["session_id"] for s in history], key="history_session",
                                      format_func=lambda value: next(f"{display_date(s['session_date'])} — {s['account_count']} accounts"
                                                                      for s in history if s["session_id"] == value))
            session = next(s for s in history if s["session_id"] == session_id)
        accounts = repository.accounts(session["session_id"])
        st.subheader("Today's Scrub" if session["session_id"] == today["session_id"] else "Previous Scrub")
        st.caption(display_date(session["session_date"]))
        values = [len(accounts)] + [sum(a["status"] == status for a in accounts) for status in STATUSES]
        for column, label, value in zip(st.columns(4), ["Accounts", "New", "Reviewed", "Rejected"], values):
            column.metric(label, value)
        if "status_error" in st.session_state:
            st.error("Could not save status: " + st.session_state.pop("status_error"))
        if "review_message" in st.session_state:
            st.success(st.session_state.pop("review_message"))
        if page != "History" and st.session_state.get("search_summary"):
            summary = st.session_state["search_summary"]
            st.success(f"{summary['added']} new accounts added · {summary['duplicates']} already in today's list")
            st.caption(f"{summary['candidates']} candidates · {summary['below_threshold']} below account threshold · "
                       f"{summary['limited']} eligible beyond result limit")
            for warning in summary["warnings"]:
                st.warning(warning)
        if page == "Account Discovery":
            if settings.account == "leadiq":
                st.caption("Live account signals currently score at most 55. Discovery fetches one bounded page; rankings describe that pool. "
                           "Find Accounts also searches people for newly added companies.")
            filters_form(service, settings)
        scrub_table(accounts, session["session_id"], repository, service)
    except (LeadIQError, ProviderError, ValueError, sqlite3.Error, OSError) as exc:
        st.error(f"Account Finder: {exc}")


if __name__ == "__main__":
    main()
