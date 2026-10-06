"""Prospect filters and ranking. Skills support ranking, never hard filtering."""
from dataclasses import dataclass

from personas import PERSONAS, SKILL_GROUPS, matching_personas, normalize_text, title_matches
from prospect_scoring import prospect_rank_key, score_prospect


@dataclass(frozen=True)
class ProspectFilters:
    persona: str | None = None
    job_titles: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    skill_groups: tuple[str, ...] = ()
    min_score: int = 0
    limit: int = 3

    def __post_init__(self):
        if self.persona and self.persona not in PERSONAS:
            raise ValueError("Unknown persona: " + self.persona)
        if self.persona == "Custom" and not any(t.strip() for t in self.job_titles):
            raise ValueError("Custom persona requires at least one job title.")
        if set(self.skill_groups) - set(SKILL_GROUPS):
            raise ValueError("Unknown skill group selected.")
        if not 0 <= self.min_score <= 100 or self.limit < 1:
            raise ValueError("Prospect score must be 0–100 and prospect limit must be positive.")


def rank_prospects(prospects, company_id, filters=None):
    filters = filters or ProspectFilters()
    rows = []
    for prospect in prospects:
        if prospect["company_id"] != company_id:
            continue
        if filters.persona and filters.persona != "Custom" and filters.persona not in matching_personas(prospect["job_title"]):
            continue
        if filters.job_titles and not any(title_matches(prospect["job_title"], t) for t in filters.job_titles):
            continue
        if filters.seniorities and normalize_text(prospect["seniority"]) not in {normalize_text(s) for s in filters.seniorities}:
            continue
        if filters.functions and normalize_text(prospect["function"]) not in {normalize_text(f) for f in filters.functions}:
            continue
        score = score_prospect(prospect, filters.persona, filters.job_titles, filters.skill_groups)
        if score["total_score"] >= filters.min_score:
            rows.append({**prospect, "prospect_score": score})
    rows.sort(key=prospect_rank_key)
    return {"results": rows[:filters.limit], "eligible_count": len(rows)}


def find_better_prospects(company_id, provider, filters=None):
    """Reload every person for this exact company and return the highest ranked."""
    records = provider.get_prospects(company_id, filters=filters) if filters else provider.get_prospects(company_id)
    return rank_prospects(records, company_id, filters)
