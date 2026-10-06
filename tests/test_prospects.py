"""Deterministic prospect contracts and regressions; no network or real people."""
import json
from collections import Counter
from dataclasses import replace
from unittest.mock import patch

import pytest

from config import PROJECT_DIR
from discovery import discover_accounts
from main import main
from personas import match_skills, matching_personas, normalize_title, title_matches
from prospect_discovery import ProspectFilters, find_better_prospects, rank_prospects
from prospect_models import validate_prospect
from prospect_providers import MockProspectProvider, get_prospect_provider
from prospect_scoring import score_prospect
from providers import MockLeadIQProvider, ProviderError
from sales_navigator import sales_navigator_link
from service import create_service


@pytest.fixture
def person():
    return dict(prospect_id="p1", company_id="c1", first_name="Mira", last_name="Fenquill",
                full_name="Mira Fenquill", job_title="Head of IT", seniority="Head",
                function="Information Technology", country="United Kingdom", city="Manchester",
                linkedin_url=None, skills=["Intune", "Azure", "Active Directory", "PowerShell", "Microsoft 365"],
                email_available=True, phone_available=False)


@pytest.mark.parametrize("title", ["IT Director", "Director of IT", "Director, Information Technology",
                                    "Director - IT", "Director of Information Technology (UK)"])
def test_title_variations(title):
    assert title_matches(title, "Director of IT")
    assert "IT Decision Makers" in matching_personas(title)


def test_title_normalization():
    assert normalize_title("  VP, IT ") == "vice president information technology"
    assert normalize_title("CIO") == normalize_title("Chief Information Officer")


@pytest.mark.parametrize("title", ["Sales Director", "Marketing Manager", "Assistant to Head of IT",
                                    "HR Manager", "Finance Manager", "Business Development Manager"])
def test_unrelated_titles_do_not_inherit_persona(title):
    assert not matching_personas(title)
    assert not title_matches(title, "IT Director")


@pytest.mark.parametrize("title,persona", [("Head of IT", "IT Decision Makers"),
    ("IT Infrastructure Manager", "Infrastructure"), ("IT Operations Manager", "IT Operations"),
    ("CISO", "Security"), ("IT Support Manager", "Service Desk")])
def test_persona_groups(title, persona):
    assert persona in matching_personas(title)


def test_explained_prospect_score(person):
    result = score_prospect(person)
    assert result["total_score"] == 92
    assert result["components"] == {"title": 30, "seniority": 17, "function": 15, "skills": 16,
                                     "location": 5, "decision_influence": 9}
    assert result["matched_persona"] == "IT Decision Makers"
    assert len(result["matched_skills"]) == 5
    assert result["positive_reasons"] == result["reasons"]
    assert result["score_band"] == "Exceptional"


def test_skill_aliases_and_no_double_count():
    skills = match_skills(["Intune", "Microsoft Intune", "Azure", "AZURE", "Marketing"])
    assert skills == ["Intune", "Azure"]
    assert match_skills(["PowerShell", "Intune"], ["Automation"]) == ["PowerShell"]


def test_strong_persona_no_skills_kept(person):
    person["skills"] = []
    score = score_prospect(person)
    assert score["total_score"] == 76
    assert score["components"]["skills"] == 0
    assert score["weak_signals"]
    # Selecting a skill group does not impose a skills prerequisite.
    ranked = rank_prospects([person], "c1", ProspectFilters(persona="IT Decision Makers",
                           skill_groups=("Endpoint Management",), min_score=70))
    assert len(ranked["results"]) == 1


def test_weak_title_with_strong_skills(person):
    person.update(job_title="Systems Administrator", seniority="Senior")
    result = score_prospect(person)
    assert result["total_score"] == 62
    assert result["components"]["title"] == 15
    assert result["matched_persona"] is None


def test_irrelevant_person(person):
    person.update(job_title="Marketing Manager", seniority="Manager", function="Marketing",
                  skills=["Campaign Planning", "Content Strategy"])
    assert score_prospect(person)["total_score"] == 0


@pytest.mark.parametrize("level,points", [("C-Level", 20), ("VP", 19), ("Director", 18),
    ("Head", 17), ("Manager", 12), ("Senior", 8), ("Individual Contributor", 4), ("Unknown", 0)])
def test_seniority(person, level, points):
    person["seniority"] = level
    assert score_prospect(person)["components"]["seniority"] == points


def test_job_title_and_persona_filters(person):
    alternative = {**person, "prospect_id": "p2", "job_title": "Director, Information Technology"}
    irrelevant = {**person, "prospect_id": "p3", "job_title": "Marketing Manager"}
    records = [person, alternative, irrelevant]
    filtered = rank_prospects(records, "c1", ProspectFilters(job_titles=("Director of IT",)))
    assert [p["prospect_id"] for p in filtered["results"]] == ["p2"]
    assert len(rank_prospects(records, "c1", ProspectFilters(persona="IT Decision Makers"))["results"]) == 2
    assert len(rank_prospects(records, "c1", ProspectFilters(persona="Custom", job_titles=("Head of IT", "IT Director")))["results"]) == 2


def test_filter_and_per_company_limit(person):
    wrong_company = {**person, "company_id": "other", "prospect_id": "other"}
    lower = {**person, "prospect_id": "low", "skills": []}
    records = [lower, wrong_company, person]
    result = rank_prospects(records, "c1", ProspectFilters(limit=1))
    assert result["eligible_count"] == 2
    assert result["results"][0]["prospect_id"] == "p1"
    assert len(rank_prospects(records, "c1", ProspectFilters(min_score=90))["results"]) == 1
    assert not rank_prospects(records, "c1", ProspectFilters(seniorities=("Manager",)))["results"]
    assert not rank_prospects(records, "c1", ProspectFilters(functions=("Sales",)))["results"]


def test_ranking_and_deterministic_ties(person):
    records = [{**person, "prospect_id": "poor", "skills": []},
               {**person, "prospect_id": "p2"}, person]
    scores = rank_prospects(records, "c1")["results"]
    assert [p["prospect_id"] for p in scores] == ["p1", "p2", "poor"]


def test_high_account_poor_first_contact_better_prospects(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    service = create_service()
    account = next(c for c in service.search_accounts(limit=50)["results"] if c["company_id"] == "MOCK-LIQ-025")
    assert account["score"]["total_score"] == 100
    before = json.dumps(account, sort_keys=True)
    assert service.current_prospect(account["company_id"])["prospect_score"]["total_score"] < 50
    result = service.find_better_prospects(account["company_id"])
    assert len(result["results"]) == 3
    assert result["results"][0]["prospect_score"]["total_score"] > 85
    assert all(p["company_id"] == account["company_id"] for p in result["results"])
    assert json.dumps(account, sort_keys=True) == before
    # Even a search with zero qualifying people leaves the same account ranked.
    assert not service.find_better_prospects(account["company_id"], ProspectFilters(job_titles=("Astronaut",)))["results"]
    assert account in service.search_accounts(limit=50)["results"]


def test_find_better_loads_all_same_company(person):
    class FakeProvider:
        def get_prospects(self, company_id):
            assert company_id == "c1"
            return [{**person, "prospect_id": "low", "skills": []}, person]
    assert find_better_prospects("c1", FakeProvider())["results"][0]["prospect_id"] == "p1"


def test_fixture_contract_and_provider_copies():
    rows = json.loads((PROJECT_DIR / "mock_prospects.json").read_text())
    companies = MockLeadIQProvider().get_companies()
    counts = Counter(p["company_id"] for p in rows)
    assert set(counts) == {c["company_id"] for c in companies}
    assert all(4 <= n <= 10 for n in counts.values())
    assert len({p["prospect_id"] for p in rows}) == len(rows)
    assert all(validate_prospect(p) and p["linkedin_url"] is None for p in rows)
    provider = MockProspectProvider()
    people = provider.get_prospects("MOCK-LIQ-025")
    people[0]["skills"].append("Mutation")
    assert "Mutation" not in provider.get_prospects("MOCK-LIQ-025")[0]["skills"]
    assert provider.get_prospects("unknown") == []


def test_mock_search_network_isolation(monkeypatch, capsys):
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("No network")):
        assert len(get_prospect_provider().get_prospects("MOCK-LIQ-025")) >= 4
        assert main(["prospects", "--company-id", "MOCK-LIQ-025", "--prospects-per-company", "3"]) == 0
    assert "Demo mode" in capsys.readouterr().out


def test_live_prospect_provider_without_credentials(monkeypatch):
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    with patch("requests.sessions.Session.request", side_effect=AssertionError("No network")):
        from leadiq import LeadIQError
        with pytest.raises(LeadIQError, match="not configured"):
            get_prospect_provider().get_prospects("any")


@pytest.mark.parametrize("kwargs", [{"persona": "Unknown"}, {"persona": "Custom"},
    {"min_score": 101}, {"limit": 0}, {"skill_groups": ("Unknown",)}])
def test_invalid_filters(kwargs):
    with pytest.raises(ValueError):
        ProspectFilters(**kwargs)


def test_invalid_fixture_data(tmp_path):
    path = tmp_path / "prospects.json"
    path.write_text('{"wrong": "shape"}')
    with pytest.raises(ProviderError, match="Cannot load"):
        MockProspectProvider(path).get_prospects("any")


def test_status_filter_extension_preserves_account_summary():
    companies = MockLeadIQProvider().get_companies()
    from providers import MockSalesforceProvider
    accounts = MockSalesforceProvider().get_accounts()
    result = discover_accounts(companies, accounts, salesforce_statuses=["POSSIBLE_MATCH"])
    assert result["summary"]["NET_NEW"] == 24
    assert len(result["results"]) == 4
    assert not discover_accounts(companies, accounts, salesforce_statuses=[])["results"]


def test_sales_navigator_helper(person):
    from urllib.parse import parse_qs, urlsplit
    link = sales_navigator_link(person, "Forge & Sons")
    assert link["is_search"]
    assert parse_qs(urlsplit(link["url"]).query)["keywords"] == ["Mira Fenquill Forge & Sons"]
    real = {**person, "linkedin_url": "https://www.linkedin.com/in/verified-profile"}
    assert sales_navigator_link(real, "Forge")["url"] == real["linkedin_url"]
    for unsafe in ["javascript:alert(1)", "https://www.linkedin.com.evil.test/in/name", "https://user@linkedin.com/in/name"]:
        assert sales_navigator_link({**person, "linkedin_url": unsafe}, "Forge")["is_search"]
