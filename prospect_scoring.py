"""Explainable, calibrated prospect quality. Never includes account quality."""
import re

from matching import normalize_country
from personas import (PERSONAS, PERSONA_TIERS, classify_authority, matching_personas,
                      match_skills, normalize_skill, normalize_text)

# All scoring knobs are kept here; authority mappings live alongside personas.
WEIGHTS = {"title": 30, "seniority": 20, "function": 15, "skills": 20,
           "location": 5, "decision_influence": 10}
SENIORITY_FIT = {"c level": 1, "c suite": 1, "chief": 1, "vp": 0.95, "vice president": 0.95,
                 "director": 0.9, "head": 0.85, "manager": 0.6, "senior": 0.4,
                 "individual contributor": 0.2, "entry": 0.1}
FUNCTIONS = {"information technology", "it", "infrastructure", "it operations", "security",
             "cybersecurity", "information security"}
EXCLUDED_DEPARTMENTS = {"sales", "marketing", "hr", "human resources", "finance", "accounting",
                        "recruiting", "recruitment", "business development"}
PENALTIES = {"junior": 20, "support": 10, "non_it": 30, "excluded_department": 40,
             "student": 40, "recruiter": 40, "consultant_internal": 5,
             "consultant_unclear": 10, "consultant_external": 15}
SCORE_BANDS = ((90, "Exceptional"), (80, "Strong"), (70, "Good"), (55, "Secondary"), (0, "Weak"))

# Relevance units, not a raw skill count. Undefined skills earn zero.
# Categories prevent synonyms or many similar tools from supplying full breadth.
SKILL_RELEVANCE = {
    "endpoint management": (2, "endpoint"), "intune": (2, "endpoint"),
    "sccm": (2, "endpoint"), "jamf": (2, "endpoint"), "device management": (2, "endpoint"),
    "mdm": (2, "endpoint"), "rmm": (2, "endpoint"),
    "patch management": (2, "security"), "active directory": (2, "infrastructure"),
    "entra id": (2, "infrastructure"), "windows server": (2, "infrastructure"),
    "powershell": (2, "automation"), "it automation": (2, "automation"),
    "automation": (2, "automation"), "scripting": (1.5, "automation"), "python": (1, "automation"),
    "networking": (1.5, "infrastructure"), "infrastructure management": (2, "infrastructure"),
    "hyper v": (1.5, "infrastructure"), "windows": (1, "infrastructure"),
    "azure": (1, "cloud"), "microsoft 365": (1, "cloud"), "vmware": (1, "infrastructure"),
    "itil": (1, "itsm"), "servicenow": (1, "itsm"), "jira service management": (1, "itsm"),
    "help desk": (1, "itsm"), "service desk": (1, "itsm"),
    "it operations": (2, "operations"), "systems administration": (2, "operations"),
    "it management": (1.5, "operations"), "infrastructure operations": (2, "operations"),
    "endpoint security": (2, "security"), "vulnerability management": (2, "security"),
    "cybersecurity": (1.5, "security"), "microsoft defender": (2, "security"),
    "project management": (0.2, "generic"), "agile": (0.2, "generic"),
    "scrum": (0.2, "generic"), "business analysis": (0.2, "generic"),
}
# Explicit persona preferences override relevance, so ITSM can matter more to a
# Service Desk Manager than a list of virtualization tools.
PERSONA_SKILL_OVERRIDES = {
    "Infrastructure": {"vmware": 2, "azure": 1.5, "hyper v": 2, "networking": 2},
    "IT Operations": {"servicenow": 1.5, "itil": 1.5, "jira service management": 1.5},
    "Service Desk": {"itil": 2, "servicenow": 2, "jira service management": 2,
                     "help desk": 2, "service desk": 2, "vmware": 0.4, "hyper v": 0.4,
                     "windows server": 1, "azure": 0.5},
    "Security": {"endpoint security": 2.5, "microsoft defender": 2.5,
                 "vulnerability management": 2.5, "patch management": 2.5,
                 "cybersecurity": 2, "vmware": 0.5},
}
SKILL_UNITS_FOR_FULL_SCORE = 10
SKILL_CATEGORY_UNIT_CAP = 4
PERSONA_CATEGORY_CAPS = {"Security": {"security": 8}, "Service Desk": {"itsm": 6}}
SKILL_BREADTH_TARGET = 3
SKILL_DEPTH_SHARE = 0.7


def score_band(score):
    if not 0 <= score <= 100:
        raise ValueError("Prospect score must be between 0 and 100.")
    return next(label for minimum, label in SCORE_BANDS if score >= minimum)


def _skill_evidence(skills, persona, groups):
    matched = match_skills(skills, groups)
    evidence, categories = [], {}
    for skill in matched:
        key = normalize_skill(skill)
        base, category = SKILL_RELEVANCE.get(key, (0, "unknown"))
        units = PERSONA_SKILL_OVERRIDES.get(persona, {}).get(key, base)
        if units <= 0:
            continue
        evidence.append({"skill": skill, "relevance_units": units, "category": category})
        categories[category] = categories.get(category, 0) + units
    # Focused security/ITSM evidence can legitimately be deep within a specialty.
    caps = PERSONA_CATEGORY_CAPS.get(persona, {})
    units = sum(min(caps.get(category, SKILL_CATEGORY_UNIT_CAP), value)
                for category, value in categories.items())
    # Generic delivery skills contribute a little depth but cannot establish
    # technical breadth on their own.
    breadth = len(set(categories) - {"generic", "unknown"})
    factor = SKILL_DEPTH_SHARE + (1 - SKILL_DEPTH_SHARE) * min(breadth / SKILL_BREADTH_TARGET, 1)
    fraction = min(units / SKILL_UNITS_FOR_FULL_SCORE, 1) * factor
    return round(WEIGHTS["skills"] * fraction), evidence, breadth


def score_prospect(prospect, persona=None, custom_titles=None, skill_groups=None):
    """Return independent score, band, components, positives, deductions and gaps.

    Custom-title selection only filters candidates; it cannot promote an unrelated
    role into a buyer. Authority is determined by explicit title rules. Relevant
    skills never become a prerequisite, and negatives are applied after positives.
    """
    if sum(WEIGHTS.values()) != 100 or any(w < 0 for w in WEIGHTS.values()):
        raise ValueError("Prospect weights must be non-negative and total 100.")
    if persona and persona not in PERSONAS:
        raise ValueError("Unknown persona: " + persona)
    title = prospect.get("job_title") or ""
    normalized_title = normalize_text(title)
    authority = classify_authority(title)
    personas = matching_personas(title)
    # Skill relevance follows the actual role, even under a Custom filter.
    actual_persona = persona if persona in personas else personas[0] if personas else None
    components = {key: 0 for key in WEIGHTS}
    reasons, missing, penalties = [], [], []

    def award(key, fraction, reason):
        components[key] = round(WEIGHTS[key] * fraction)
        if components[key]:
            reasons.append(reason)

    def penalize(code, reason):
        penalties.append({"code": code, "points": PENALTIES[code], "reason": reason})

    award("title", authority["title_fit"], f"{title}: {PERSONA_TIERS[authority['tier']]}")
    award("decision_influence", authority["influence"], "Likely purchasing influence for " + title)
    if not authority["role"]:
        missing.append("No explicit purchasing-relevant IT role identified")
    seniority = normalize_text(prospect.get("seniority", ""))
    junior = bool(re.search(r"\b(junior|intern|trainee)\b", normalized_title + " " + seniority))
    seniority_fit = SENIORITY_FIT.get(seniority, 0)
    if junior:
        seniority_fit = min(seniority_fit, SENIORITY_FIT["entry"])
        penalize("junior", "Junior, intern or trainee role")
    if re.search(r"\b(support specialist|support technician|help desk analyst|service desk analyst)\b", normalized_title):
        penalize("support", "Support specialist/analyst role has limited buying authority")
    award("seniority", seniority_fit, "Seniority: " + (prospect.get("seniority") or "Unknown"))
    if seniority not in SENIORITY_FIT:
        missing.append("Seniority is unknown")
    elif seniority_fit < SENIORITY_FIT["head"]:
        missing.append("No explicit purchasing-level seniority")
    function = normalize_text(prospect.get("function", ""))
    if function in EXCLUDED_DEPARTMENTS or re.search(
            r"\b(sales|marketing|hr|finance|accounting|recruiting|recruitment)\b", normalized_title):
        penalize("excluded_department", "Sales, marketing, HR, finance or recruiting role")
    elif function in FUNCTIONS:
        award("function", 1, "Relevant IT/security department")
    elif function:
        penalize("non_it", "Non-IT department")
    else:
        missing.append("Function/department is unknown")
    if re.search(r"\bstudent\b", normalized_title + " " + seniority):
        penalize("student", "Student without established internal purchasing ownership")
    if re.search(r"\brecruiter\b", normalized_title):
        penalize("recruiter", "Recruiter rather than internal IT buyer")
    if re.search(r"\bconsultant\b", normalized_title):
        if re.search(r"\b(internal|inhouse)\b|in house", normalized_title):
            code = "consultant_internal"
        elif re.search(r"\b(external|freelance|contract)\b", normalized_title):
            code = "consultant_external"
        else:
            code = "consultant_unclear"
        penalize(code, "Consultant: internal purchasing ownership is not established")
    points, evidence, breadth = _skill_evidence(prospect.get("skills", []), actual_persona, skill_groups)
    components["skills"] = points
    if points:
        reasons.append(f"{points}/{WEIGHTS['skills']} skill points from weighted relevance across {breadth} technical categories")
        reasons.extend("Relevant skill: " + e["skill"] for e in evidence)
    else:
        missing.append("No relevant skill evidence; skills are optional ranking signals")
    if breadth < SKILL_BREADTH_TARGET:
        missing.append("Limited breadth of relevant technical skill evidence")
    if not skill_groups or "Security" in skill_groups:
        if not {normalize_skill(e["skill"]) for e in evidence} & {
                "endpoint security", "microsoft defender", "patch management", "vulnerability management"}:
            missing.append("No endpoint-security or patch-management skill evidence")
    if normalize_country(prospect.get("country")) in {"united kingdom", "ireland"}:
        award("location", 1, "UK/Ireland prospect location")
    else:
        missing.append("Location outside target geography or unknown")
    total_penalty = sum(p["points"] for p in penalties)
    total = max(0, min(100, sum(components.values()) - total_penalty))
    return {"total_score": total, "score_band": score_band(total), "components": components,
            "positive_reasons": reasons, "reasons": reasons, "penalties": penalties,
            "penalty_total": total_penalty, "matched_persona": actual_persona,
            "persona_tier": authority["tier"], "persona_tier_label": PERSONA_TIERS[authority["tier"]],
            "matched_skills": [e["skill"] for e in evidence], "skill_evidence": evidence,
            "missing_signals": missing, "weak_signals": missing}


def prospect_rank_key(prospect):
    """Lower tier number has higher authority; stable final fallback is name/ID."""
    score = prospect["prospect_score"]
    return (-score["total_score"], score["persona_tier"],
            -score["components"]["decision_influence"], -score["components"]["seniority"],
            -len(score["matched_skills"]), (prospect.get("full_name") or "").casefold(), prospect["prospect_id"])
