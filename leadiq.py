"""LeadIQ transport and company discovery, independent of the CLI."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from config import use_mock_data

ENDPOINT = "https://api.leadiq.com/graphql"
AUTH_QUERY = "query TestAuth { account { plans { name } } }"
COMPANY_SEARCH_QUERY = """
query DiscoverCompanies($input: GroupedSearchInput!) {
  groupedAdvancedSearch(input: $input) {
    companies {
      company { id name domain employeeCount industry country city }
      totalContactsInCompany
    }
  }
}
"""


class LeadIQError(Exception):
    """An actionable error safe to display to the CLI user."""


def normalize_domain(domain):
    """Return a lowercase hostname, without www, port, path or trailing dot.

    Preserve other subdomains: this is not a registrable-domain extractor.
    Missing/empty input returns None; malformed input raises ValueError.
    """
    if domain is None or not str(domain).strip():
        return None
    value = str(domain).strip()
    try:
        parsed = urlsplit(value if "://" in value or value.startswith("//") else "//" + value)
        if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
            raise ValueError("Use a domain or an HTTP/HTTPS URL.")
        hostname = parsed.hostname
        if not hostname or parsed.username is not None or any(c.isspace() for c in hostname):
            raise ValueError("Invalid company domain.")
        hostname = hostname.lower().rstrip(".").encode("idna").decode("ascii")
        return hostname[4:] if hostname.startswith("www.") else hostname
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid company domain: use a hostname or HTTP/HTTPS URL.") from exc


def leadiq_request(query, variables=None):
    """POST GraphQL and return its data object; raise LeadIQError on failure.

    LEADIQ_API_KEY must be the Secret Base64 API key, already encoded.
    Partial GraphQL responses are rejected so discovery never silently loses data.
    """
    if use_mock_data():
        raise LeadIQError("LeadIQ API requests are disabled while USE_MOCK_DATA=true.")
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    key = os.getenv("LEADIQ_API_KEY", "").strip()
    if not key or key == "your_leadiq_api_key_here":
        raise LeadIQError("Missing LeadIQ credentials. Copy .env.example to .env and set "
                          "LEADIQ_API_KEY to your Secret Base64 API key.")
    try:
        response = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Basic {key}", "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
            timeout=(10, 30),
        )
    except requests.Timeout as exc:
        raise LeadIQError("LeadIQ request timed out. Try again later.") from exc
    except requests.ConnectionError as exc:
        raise LeadIQError("Cannot connect to LeadIQ. Check your internet connection and DNS.") from exc
    except requests.RequestException as exc:
        raise LeadIQError("Could not send the LeadIQ request. Check your connection and configuration.") from exc

    if response.status_code in (401, 403):
        raise LeadIQError("LeadIQ authentication or access failed. Check your Secret Base64 "
                          "API key and your account's API permissions.")
    if response.status_code == 429:
        retry = response.headers.get("Retry-After")
        raise LeadIQError("LeadIQ rate limit reached. " +
                          (f"Retry after {retry}." if retry else "Wait before trying again."))
    try:
        payload = response.json()
    except ValueError as exc:
        raise LeadIQError(f"LeadIQ returned a non-JSON response (HTTP {response.status_code}). "
                          "Try again later.") from exc
    if isinstance(payload, dict) and payload.get("errors"):
        errors = payload["errors"]
        if not isinstance(errors, list):
            errors = [errors]
        messages = []
        codes = []
        for error in errors:
            if isinstance(error, dict):
                messages.append(str(error.get("message", "Unspecified GraphQL error")))
                extension = error.get("extensions")
                if isinstance(extension, dict):
                    codes.append(str(extension.get("code", "")))
            else:
                messages.append(str(error))
        detail = "; ".join(messages).replace(key, "[redacted]")
        classification = (detail + " " + " ".join(codes)).lower()
        if any(term in classification for term in ("rate", "too_many_requests", "throttl")):
            hint = "Wait before retrying; check your LeadIQ request limits."
        elif any(term in classification for term in ("unauth", "forbidden", "permission", "api key")):
            hint = "Check your API key and account permissions."
        elif any(term in classification for term in ("cannot query", "unknown", "not defined", "validation")):
            hint = "Your schema differs from the public reference. Update AUTH_QUERY or " \
                   "COMPANY_SEARCH_QUERY and the search input mapping in leadiq.py " \
                   "against your LeadIQ schema; no fallback fields are guessed."
        else:
            hint = "Check your LeadIQ API access, credits and query parameters."
        raise LeadIQError(f"LeadIQ GraphQL error: {detail}. {hint}")
    if not response.ok:
        raise LeadIQError(f"LeadIQ HTTP error {response.status_code}. "
                          "Check API access and parameters; retry later for server errors.")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise LeadIQError("LeadIQ returned an unexpected response: missing GraphQL data object.")
    return payload["data"]


def test_auth():
    """Verify access with the documented, small account query."""
    data = leadiq_request(AUTH_QUERY)
    if not isinstance(data.get("account"), dict):
        raise LeadIQError("LeadIQ returned no account. Verify API access and the account query schema.")
    return data["account"]


def search_companies(country=None, min_employees=None, max_employees=None,
                     industry=None, limit=20):
    """Return one page of companies from documented grouped advanced search."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2147483647:
        raise ValueError("Result limit must be a positive GraphQL integer.")
    for bound in (min_employees, max_employees):
        if bound is not None and (not isinstance(bound, int) or isinstance(bound, bool)
                                  or not 0 <= bound <= 2147483647):
            raise ValueError("Employee bounds must be non-negative GraphQL integers.")
    if min_employees is not None and max_employees is not None and min_employees > max_employees:
        raise ValueError("Minimum employees cannot exceed maximum employees.")
    filters = {}
    if country and country.strip():
        filters["locations"] = [{"country": country.strip()}]
    if industry and industry.strip():
        filters["industries"] = [industry.strip()]
    size = {key: value for key, value in (("min", min_employees), ("max", max_employees))
            if value is not None}
    if size:
        filters["sizes"] = [size]
    data = leadiq_request(COMPANY_SEARCH_QUERY, {"input": {"companyFilter": filters, "limit": limit}})
    result = data.get("groupedAdvancedSearch")
    if not isinstance(result, dict) or not isinstance(result.get("companies"), list):
        raise LeadIQError("Unexpected company-search response. Check COMPANY_SEARCH_QUERY against your schema.")
    companies = []
    for entry in result["companies"][:limit]:
        if not isinstance(entry, dict) or not isinstance(entry.get("company"), dict):
            raise LeadIQError("LeadIQ returned an invalid company record.")
        company = entry["company"]
        try:
            domain = normalize_domain(company.get("domain"))
        except ValueError as exc:
            raise LeadIQError("LeadIQ returned a malformed company domain; review the API data.") from exc
        companies.append({
            "id": company.get("id"), "name": company.get("name"), "domain": domain,
            "employee_count": company.get("employeeCount"), "industry": company.get("industry"),
            "country": company.get("country"), "city": company.get("city"),
            "matching_contacts": entry.get("totalContactsInCompany"), "linkedin_url": None,
        })
    return companies
