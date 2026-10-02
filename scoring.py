"""Editable ICP model. Unknown signals earn no points."""
from matching import normalize_country

WEIGHTS = {
    "employee_size": 25, "industry": 20, "geography": 10, "it_headcount": 15,
    "locations": 10, "it_hiring": 10, "growth_signal": 10,
}
TARGET_INDUSTRIES = {"manufacturing", "technology", "logistics"}
EMPLOYEE_RANGE = (500, 5000)
IT_HEADCOUNT_TIERS = ((50, 1.0), (20, 2 / 3), (5, 1 / 3))
LOCATION_TIERS = ((5, 1.0), (2, 0.5))


def score_company(company):
    if any(weight < 0 for weight in WEIGHTS.values()) or sum(WEIGHTS.values()) != 100:
        raise ValueError("ICP weights must be non-negative and sum to 100.")
    components = {key: 0 for key in WEIGHTS}
    reasons = []

    def award(key, fraction, reason):
        points = round(WEIGHTS[key] * fraction)
        components[key] = points
        if points:
            reasons.append(reason)

    employees = company.get("employee_count")
    if employees is not None and EMPLOYEE_RANGE[0] <= employees <= EMPLOYEE_RANGE[1]:
        award("employee_size", 1, "Employee count fits ICP (500–5000)")
    if (company.get("industry") or "").strip().casefold() in TARGET_INDUSTRIES:
        award("industry", 1, "Target industry")
    if normalize_country(company.get("country")) in {"united kingdom", "ireland"}:
        award("geography", 1, "UK/Ireland geography")
    for threshold, fraction in IT_HEADCOUNT_TIERS:
        if (company.get("it_headcount") or 0) >= threshold:
            award("it_headcount", fraction, f"IT headcount at least {threshold}")
            break
    for threshold, fraction in LOCATION_TIERS:
        if (company.get("number_of_locations") or 0) >= threshold:
            award("locations", fraction, f"Multiple locations (at least {threshold})")
            break
    if company.get("it_hiring") is True:
        award("it_hiring", 1, "IT hiring detected")
    if company.get("growth_signal") is True:
        award("growth_signal", 1, "Growth signal detected")
    return {"total_score": sum(components.values()), "components": components, "reasons": reasons}
