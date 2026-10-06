"""SDR prioritization scenarios, calibration ranges and tie-breaking contracts."""
import json
from itertools import permutations

import pytest

from config import PROJECT_DIR
from personas import classify_authority
from prospect_discovery import rank_prospects
from prospect_scoring import score_band, score_prospect


def prospect(title="Head of IT", seniority="Head", skills=None, function="Information Technology", **extra):
    return {"prospect_id": "p", "company_id": "company", "full_name": "Demo Person",
            "job_title": title, "seniority": seniority, "function": function,
            "country": "United Kingdom", "skills": skills or [], **extra}


ENDPOINT_SKILLS = ["Intune", "PowerShell", "Active Directory", "Endpoint Management", "Patch Management", "RMM"]
INFRA_SKILLS = ["Windows Server", "VMware", "Active Directory", "Azure", "PowerShell"]


def test_case_a_head_without_skills_beats_skilled_support():
    senior = score_prospect(prospect())
    support = score_prospect(prospect("IT Support Specialist", "Individual Contributor", ENDPOINT_SKILLS))
    assert senior["total_score"] == 76
    assert senior["components"]["skills"] == 0
    assert support["components"]["skills"] == 20
    assert senior["total_score"] > support["total_score"]
    assert support["score_band"] == "Weak"
    assert any(p["code"] == "support" for p in support["penalties"])


def test_case_b_director_beats_skilled_administrator():
    director = score_prospect(prospect("IT Director", "Director", ["Azure", "Microsoft 365", "Intune"]))
    admin = score_prospect(prospect("Systems Administrator", "Senior", ENDPOINT_SKILLS))
    assert director["total_score"] > admin["total_score"]
    assert director["score_band"] == "Strong"
    assert admin["persona_tier"] == 3


def test_case_c_infrastructure_buyer_is_strong():
    buyer = score_prospect(prospect("Infrastructure Manager", "Manager", INFRA_SKILLS))
    assert 80 <= buyer["total_score"] < 90
    assert buyer["score_band"] == "Strong"
    assert buyer["persona_tier"] == 2
    assert buyer["components"]["decision_influence"] == 7


def test_case_d_marketing_stays_weak_with_technology_skills():
    for function in ("Marketing", "Information Technology"):
        score = score_prospect(prospect("Marketing Director", "Director", ENDPOINT_SKILLS + ["Azure", "Agile"], function))
        assert score["score_band"] == "Weak"
        assert score["persona_tier"] == 4
        assert score["components"]["decision_influence"] == 0
        assert "excluded_department" in [p["code"] for p in score["penalties"]]


def test_case_e_junior_intern_stays_weak():
    score = score_prospect(prospect("Junior IT Intern", "Entry", ENDPOINT_SKILLS))
    assert score["score_band"] == "Weak"
    assert {p["code"] for p in score["penalties"]} == {"junior"}
    # Conflicting seniority metadata cannot remove the junior penalty/cap.
    conflict = score_prospect(prospect("Junior Systems Administrator", "C-Level", ENDPOINT_SKILLS))
    assert conflict["components"]["seniority"] <= 2
    assert conflict["score_band"] == "Weak"


def test_case_f_authority_tie_breaking(monkeypatch):
    # Equal totals with different component/penalty evidence exercise every
    # required ordering rule independently of the scorer's numeric calibration.
    def row(pid, tier=2, influence=7, seniority=12, skills=3, name="Demo Person"):
        return {**prospect(prospect_id=pid, full_name=name), "fixture_score": {
            "total_score": 80, "persona_tier": tier,
            "components": {"decision_influence": influence, "seniority": seniority},
            "matched_skills": [str(i) for i in range(skills)]}}
    monkeypatch.setattr("prospect_discovery.score_prospect", lambda p, *args: p["fixture_score"])
    pairs = [
        (row("high", tier=1, influence=1), row("low", tier=2, influence=10)),
        (row("high", influence=8, seniority=1), row("low", influence=7, seniority=20)),
        (row("high", seniority=18, skills=1), row("low", seniority=12, skills=6)),
        (row("high", skills=4, name="Zara"), row("low", skills=3, name="Anna")),
        (row("high", name="Anna"), row("low", name="Zara")),
    ]
    for better, weaker in pairs:
        for ordering in permutations([better, weaker]):
            assert rank_prospects(ordering, "company")["results"][0]["prospect_id"] == "high"
    same_name = [row("b"), row("a")]
    assert [r["prospect_id"] for r in rank_prospects(same_name, "company")["results"]] == ["a", "b"]


def test_case_g_only_exceptional_complete_signals_reach_100():
    exceptional = score_prospect(prospect("CIO", "C-Level", ENDPOINT_SKILLS))
    assert exceptional["total_score"] == 100
    for title, seniority in (("Head of IT", "Head"), ("IT Director", "Director"),
                             ("Infrastructure Manager", "Manager"), ("Systems Administrator", "Senior")):
        assert score_prospect(prospect(title, seniority, ENDPOINT_SKILLS))["total_score"] < 100
    assert score_prospect(prospect("CIO", "C-Level"))["total_score"] < 90
    # A long list of equivalent endpoint tools is insufficiently broad for 100.
    narrow = score_prospect(prospect("CIO", "C-Level", ["Intune", "SCCM", "Jamf", "RMM", "MDM", "Device Management"]))
    assert narrow["components"]["skills"] < 10
    assert narrow["total_score"] < 90


@pytest.mark.parametrize("title,tier,influence", [
    ("CIO", 1, 10), ("VP IT", 1, 9), ("Director, Information Technology", 1, 9),
    ("Head of IT", 1, 9), ("Head of Technology", 1, 9), ("Infrastructure Manager", 2, 7),
    ("IT Operations Manager", 2, 7), ("Head of IT Operations", 2, 8), ("Systems Manager", 2, 6),
    ("IT Support Manager", 3, 5),
    ("Systems Administrator", 3, 3), ("IT Support Specialist", 4, 1), ("Sales Director", 4, 0)])
def test_authority_is_explicit_and_independent_of_skills(title, tier, influence):
    authority = classify_authority(title)
    assert authority["tier"] == tier
    plain = score_prospect(prospect(title, "Manager"))
    skilled = score_prospect(prospect(title, "Manager", ENDPOINT_SKILLS))
    assert plain["components"]["decision_influence"] == skilled["components"]["decision_influence"] == influence


def test_custom_filter_cannot_promote_irrelevant_persona():
    person = prospect("Marketing Director", "Director", ENDPOINT_SKILLS, "Marketing")
    assert score_prospect(person, persona="Custom", custom_titles=["Marketing Director"])["score_band"] == "Weak"


def test_persona_specific_skill_relevance():
    desk = prospect("Service Desk Manager", "Manager", ["ITIL", "ServiceNow", "Jira Service Management", "Endpoint Management"])
    virtual = {**desk, "skills": ["VMware", "Hyper-V", "Windows Server", "Azure"]}
    assert score_prospect(desk)["components"]["skills"] > score_prospect(virtual)["components"]["skills"]
    security = prospect("IT Security Manager", "Manager", ["Endpoint Security", "Defender", "Vulnerability Management", "Patch Management"])
    unrelated = {**security, "skills": ["Azure", "Microsoft 365", "VMware", "Project Management"]}
    assert score_prospect(security)["matched_persona"] == "Security"
    assert score_prospect(security)["components"]["skills"] > score_prospect(unrelated)["components"]["skills"]


def test_irrelevant_and_duplicated_skills_do_not_inflate_score():
    base = prospect(skills=["Intune"])
    inflated = {**base, "skills": ["Intune", "Microsoft Intune", "INTUNE", "SEO", "Salesforce", "Accounting"]}
    assert score_prospect(base)["components"]["skills"] == score_prospect(inflated)["components"]["skills"]
    assert score_prospect(prospect(skills=["Agile", "Scrum", "Project Management", "Business Analysis"]))["components"]["skills"] <= 2


@pytest.mark.parametrize("title,code", [("Student", "student"), ("IT Recruiter", "recruiter"),
    ("External IT Consultant", "consultant_external"), ("IT Consultant", "consultant_unclear"),
    ("Internal IT Consultant", "consultant_internal")])
def test_penalties_explained_and_bounded(title, code):
    score = score_prospect(prospect(title, "Entry", ENDPOINT_SKILLS))
    assert code in {p["code"] for p in score["penalties"]}
    assert 0 <= score["total_score"] <= 100
    assert score["total_score"] == max(0, sum(score["components"].values()) - score["penalty_total"])


def test_department_penalties_are_not_double_counted():
    marketing = score_prospect(prospect("Marketing Manager", "Manager", function="Marketing"))
    assert marketing["penalty_total"] == 40
    other = score_prospect(prospect("Legal Counsel", "Senior", function="Legal"))
    assert other["penalty_total"] == 30
    unknown = score_prospect(prospect(function=""))
    assert unknown["penalty_total"] == 0
    assert "Function/department is unknown" in unknown["missing_signals"]


@pytest.mark.parametrize("score,band", [(0, "Weak"), (54, "Weak"), (55, "Secondary"), (69, "Secondary"),
    (70, "Good"), (79, "Good"), (80, "Strong"), (89, "Strong"), (90, "Exceptional"), (100, "Exceptional")])
def test_score_band_boundaries(score, band):
    assert score_band(score) == band


def test_all_mock_scores_explain_totals_and_perfect_is_rare():
    records = json.loads((PROJECT_DIR / "mock_prospects.json").read_text())
    scores = [score_prospect(p) for p in records]
    for score in scores:
        assert score["total_score"] == max(0, min(100, sum(score["components"].values()) - score["penalty_total"]))
        assert score["score_band"] == score_band(score["total_score"])
        assert score["missing_signals"] == score["weak_signals"]
    # Guard inflation, without manufacturing or altering the fictional fixtures.
    assert sum(s["total_score"] >= 90 for s in scores) / len(scores) <= 0.20
    assert sum(s["total_score"] == 100 for s in scores) / len(scores) < 0.05
