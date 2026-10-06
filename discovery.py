"""Pure filtering, matching, scoring and ranking pipeline."""
from leadiq import normalize_domain
from matching import detect_salesforce_duplicate, normalize_country
from scoring import score_company


def discover_accounts(companies, salesforce_accounts, country=None, industry=None,
                      min_employees=None, max_employees=None, min_score=0,
                      limit=20, show_all=False, salesforce_statuses=None):
    allowed_statuses = {"EXISTS", "POSSIBLE_MATCH", "NET_NEW", "NOT_CHECKED"}
    if salesforce_statuses is not None and set(salesforce_statuses) - allowed_statuses:
        raise ValueError("Unknown Salesforce status filter.")
    statuses = set(salesforce_statuses) if salesforce_statuses is not None else allowed_statuses if show_all else {"NET_NEW", "NOT_CHECKED"}
    if not 0 <= min_score <= 100:
        raise ValueError("Minimum score must be between 0 and 100.")
    if limit < 1:
        raise ValueError("Result limit must be positive.")
    if any(value is not None and value < 0 for value in (min_employees, max_employees)):
        raise ValueError("Employee bounds must be non-negative.")
    if min_employees is not None and max_employees is not None and min_employees > max_employees:
        raise ValueError("Minimum employees cannot exceed maximum employees.")
    counts = {"EXISTS": 0, "POSSIBLE_MATCH": 0, "NET_NEW": 0, "NOT_CHECKED": 0}
    results = []
    salesforce_accounts = list(salesforce_accounts) if salesforce_accounts is not None else None
    for company in companies:
        employees = company.get("employee_count")
        if country and normalize_country(company.get("country")) != normalize_country(country):
            continue
        if industry and (company.get("industry") or "").strip().casefold() != industry.strip().casefold():
            continue
        if min_employees is not None and (employees is None or employees < min_employees):
            continue
        if max_employees is not None and (employees is None or employees > max_employees):
            continue
        status = detect_salesforce_duplicate(company, salesforce_accounts) if salesforce_accounts is not None else "NOT_CHECKED"
        counts[status] += 1
        score = score_company(company)
        if score["total_score"] >= min_score and status in statuses:
            results.append({**company, "domain": normalize_domain(company.get("domain")),
                            "salesforce_status": status, "score": score})
    # Stable tie-break makes fixture and API results reproducible.
    results.sort(key=lambda row: (-row["score"]["total_score"],
                                  (row.get("company_name") or "").casefold(), row["company_id"]))
    return {"results": results[:limit], "summary": {"total_candidates": sum(counts.values()), **counts},
            "eligible_results": len(results)}
