"""Daily persistence, safe initialization/reset, and CRM-free search orchestration."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

import pytest

from config import ProviderSettings, get_settings
from database import APPLICATION_ID, SCHEMA_VERSION, connection, database_path, initialize_database
from main import main
from prospect_discovery import ProspectFilters
from repositories import ScrubRepository, local_today
from scrub_service import create_scrub_service


@pytest.fixture
def repo():
    return ScrubRepository()


def company(company_id="c1", domain="https://www.forge.example/", name="Forge Ltd", score=90, source="mock"):
    return {"company_id": company_id, "company_name": name, "domain": domain, "source": source,
            "country": "United Kingdom", "city": "London", "employee_count": 1200, "industry": "Manufacturing",
            "score": {"total_score": score}}


def test_database_initialization_and_reinitialization(repo):
    assert repo.path.is_file()
    with connection(repo.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    assert initialize_database() == repo.path


def test_today_created_and_reused_across_restart(repo):
    session = repo.ensure_session()
    assert session["session_date"] == local_today().isoformat()
    assert ScrubRepository().ensure_session() == session
    assert session["created_at"] and session["updated_at"]


def test_following_day_and_history_are_separate(repo):
    first = repo.ensure_session(date(2026, 10, 5))
    second = repo.ensure_session(date(2026, 10, 6))
    assert first["session_id"] != second["session_id"]
    for session in (first, second):
        assert repo.append(session["session_id"], [company()]) == {"added": 1, "duplicates": 0}
    assert [s["session_date"] for s in repo.sessions()] == ["2026-10-06", "2026-10-05"]
    assert all(s["account_count"] == 1 for s in repo.sessions())


def test_account_insert_and_metadata(repo):
    session = repo.ensure_session()
    assert repo.append(session["session_id"], [company()])["added"] == 1
    account = repo.accounts(session["session_id"])[0]
    assert account["domain"] == "forge.example"
    assert account["status"] == "NEW" and account["account_score_band"] == "Exceptional"
    assert account["prospects"] == [] and account["best_prospect_score"] is None
    assert account["source"] == "mock" and account["date_added"] == account["updated_at"]
    assert repo.sessions()[0]["updated_at"] >= session["updated_at"]


@pytest.mark.parametrize("second", [
    company("c2", "http://forge.example", "Different company name"),
    company("c1", "changed.example"),
    company("c1", None),
    company(None, None, "FORGE LIMITED"),
])
def test_duplicate_priority_and_counts(repo, second):
    session = repo.ensure_session()["session_id"]
    assert repo.append(session, [company(), second]) == {"added": 1, "duplicates": 1}
    assert len(repo.accounts(session)) == 1


def test_name_only_fallback_can_acquire_strong_alias(repo):
    session = repo.ensure_session()["session_id"]
    repo.append(session, [company(None, None, "Café Forge Ltd")])
    assert repo.append(session, [company("c1", "forge.example", "Cafe Forge Limited")])["duplicates"] == 1
    assert repo.append(session, [company("c1", None, "Renamed Forge")])["duplicates"] == 1
    assert repo.accounts(session)[0]["domain"] == "forge.example"
    assert repo.accounts(session)[0]["company_id"] == "c1"
    # After promotion, a name must not merge conflicting strong identities.
    assert repo.append(session, [company("c2", "other.example", "Cafe Forge Ltd")])["added"] == 1


def test_duplicate_alias_remembered_for_later_id_only_search(repo):
    session = repo.ensure_session()["session_id"]
    repo.append(session, [company()])
    repo.append(session, [company("alternate", "forge.example")])
    assert repo.append(session, [company("alternate", None, "New name")])["duplicates"] == 1


def test_same_name_conflicting_strong_identifiers_stay_distinct(repo):
    session = repo.ensure_session()["session_id"]
    assert repo.append(session, [company(), company("c2", "different.example")])["added"] == 2


def test_provider_id_is_namespaced_and_domain_is_cross_provider(repo):
    session = repo.ensure_session()["session_id"]
    assert repo.append(session, [company(domain=None), company(domain=None, source="leadiq")])["added"] == 2
    assert repo.append(session, [company("another", "shared.example"),
                                 company("live", "shared.example", source="leadiq")]) == {"added": 1, "duplicates": 1}


def test_status_persists_and_duplicate_keeps_review(repo):
    session = repo.ensure_session()["session_id"]
    repo.append(session, [company()])
    account = repo.accounts(session)[0]
    repo.set_status(account["id"], "REVIEWED")
    repo.append(session, [company(score=70)])
    stored = ScrubRepository().accounts(session)[0]
    assert stored["status"] == "REVIEWED" and stored["account_score"] == 90
    assert stored["date_added"] == account["date_added"]
    assert stored["updated_at"] > account["updated_at"]
    with pytest.raises(ValueError, match="Status"):
        repo.set_status(account["id"], "NET_NEW")


def test_ordering(repo):
    session = repo.ensure_session()["session_id"]
    repo.append(session, [company("b", "b.example", "Beta", 90), company("a", "a.example", "Alpha", 90),
                          company("low", "low.example", "Aardvark", 50)])
    assert [a["company_id"] for a in repo.accounts(session)] == ["a", "b", "low"]


def test_batch_failure_rolls_back_all_rows(repo):
    session = repo.ensure_session()["session_id"]
    with pytest.raises(sqlite3.IntegrityError):
        repo.append(session, [company(), company("invalid", "bad.example", score=101)])
    assert repo.accounts(session) == []


def test_concurrent_append_and_session_creation(repo):
    def append_once(_):
        other = ScrubRepository(repo.path)
        day = other.ensure_session()
        return other.append(day["session_id"], [company()])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(append_once, range(8)))
    assert sum(r["added"] for r in results) == 1
    assert sum(r["duplicates"] for r in results) == 7
    assert len(repo.sessions()) == 1


def test_newer_schema_is_preserved(repo):
    with connection(repo.path) as db, db:
        db.execute("PRAGMA user_version = 999")
    with pytest.raises(ValueError, match="newer schema"):
        initialize_database()
    with connection(repo.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


def test_unrecognized_database_preserved(tmp_path):
    path = tmp_path / "unrelated.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE precious_data(value TEXT)")
        db.execute("INSERT INTO precious_data VALUES('keep')")
    with pytest.raises(ValueError, match="preserved"):
        initialize_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM precious_data").fetchone()[0] == "keep"


def test_three_searches_accumulate_without_crm_or_network(repo):
    service = create_scrub_service(repo, ProviderSettings())
    with patch.object(service.discovery.salesforce_provider, "get_accounts", side_effect=AssertionError("No CRM")), \
         patch("requests.sessions.Session.request", side_effect=AssertionError("No network")):
        common = dict(country="United Kingdom", min_employees=500, max_employees=5000, min_score=60, limit=100)
        first = service.find_accounts(industry="Manufacturing", **common)
        second = service.find_accounts(industry="Logistics", **common)
        third = service.find_accounts(industry="Manufacturing", **common)
    assert first["added"] == 9 and second["added"] == 4
    assert third["added"] == 0 and third["duplicates"] == 9
    session = repo.ensure_session()["session_id"]
    assert len(ScrubRepository().accounts(session)) == 13
    assert len(repo.sessions()) == 1
    assert all("salesforce_status" not in row for row in repo.accounts(session))


def test_no_matching_people_keeps_good_account_and_find_better(repo):
    service = create_scrub_service(repo, ProviderSettings())
    filters = dict(country="United Kingdom", industry="Manufacturing", min_employees=500, max_employees=5000, limit=1)
    service.find_accounts(prospect_filters=ProspectFilters(job_titles=("Astronaut",)), **filters)
    session = repo.ensure_session()["session_id"]
    account = repo.accounts(session)[0]
    assert account["company_id"] == "MOCK-LIQ-025" and account["account_score"] == 100
    assert not account["prospects"]
    service.find_better(account, ProspectFilters())
    account = ScrubRepository().accounts(session)[0]
    assert account["account_score"] == 100 and account["best_prospect_score"] == 96
    assert account["best_prospect_name"] == "Tobin Briarvale"


def test_prospect_provider_failure_retains_accounts_without_retry_storm(repo):
    service = create_scrub_service(repo, ProviderSettings())
    from leadiq import LeadIQError
    with patch.object(service.discovery, "find_better_prospects", side_effect=LeadIQError("Rate limited", code="rate_limit")) as find:
        summary = service.find_accounts(limit=5)
    assert summary["added"] == 5 and summary["warnings"]
    assert find.call_count == 1
    assert len(repo.accounts(repo.ensure_session()["session_id"])) == 5


def test_duplicate_search_skips_people_requests(repo):
    service = create_scrub_service(repo, ProviderSettings())
    service.find_accounts(limit=2)
    with patch.object(service.discovery, "find_better_prospects", side_effect=AssertionError("No repeated people search")):
        assert service.find_accounts(limit=2)["duplicates"] == 2


def test_search_validation_precedes_provider(repo):
    service = create_scrub_service(repo, ProviderSettings())
    with patch.object(service.discovery.account_provider, "get_companies", side_effect=AssertionError("No calls")):
        with pytest.raises(ValueError, match="Minimum employees"):
            service.find_accounts(min_employees=5000, max_employees=500)


def test_ui_configuration_ignores_salesforce(monkeypatch):
    monkeypatch.setenv("SALESFORCE_PROVIDER", "api")
    assert get_settings(include_salesforce=False).salesforce == "disabled"


@pytest.mark.parametrize("answer", ["", "no", "yes"])
def test_reset_requires_exact_confirmation(repo, answer):
    repo.ensure_session()
    with patch("builtins.input", return_value=answer), patch("requests.post", side_effect=AssertionError("No API")):
        assert main(["reset-local-data"]) == 0
    assert repo.path.exists()


@pytest.mark.parametrize("arguments", [[], ["--yes"]])
def test_reset_confirmed_deletes_only_app_database(repo, arguments, tmp_path):
    repo.ensure_session()
    unrelated = tmp_path / "other.txt"
    unrelated.write_text("preserve")
    with patch("builtins.input", return_value="RESET"), patch("requests.post", side_effect=AssertionError("No API")):
        assert main(["reset-local-data", *arguments]) == 0
    assert not repo.path.exists() and unrelated.read_text() == "preserve"


def test_reset_refuses_unrelated_database(monkeypatch, tmp_path):
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE other(value TEXT)")
    monkeypatch.setenv("LOCAL_DB_PATH", str(path))
    assert main(["reset-local-data", "--yes"]) == 1
    assert path.is_file()


def test_reset_handles_noninteractive_input(repo):
    with patch("builtins.input", side_effect=EOFError):
        assert main(["reset-local-data"]) == 0
    assert database_path().exists()
