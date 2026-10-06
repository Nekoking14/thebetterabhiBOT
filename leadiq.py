"""LeadIQ transport and company discovery, independent of the CLI."""

import hashlib
import logging
from urllib.parse import urlsplit

import requests
from config import get_settings, leadiq_api_key
from leadiq_schema import AUTH_QUERY, COMPANY_SEARCH_QUERY
from response_cache import ResponseCache

ENDPOINT = "https://api.leadiq.com/graphql"


class LeadIQError(Exception):
    """An actionable error safe to display to the CLI user."""

    def __init__(self, message, code="request"):
        super().__init__(message)
        self.code = code


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


def leadiq_request(query, variables=None, *, allow_live=False):
    """POST GraphQL and return its data object; raise LeadIQError on failure.

    LEADIQ_API_KEY must be the Secret Base64 API key, already encoded.
    Partial GraphQL responses are rejected so discovery never silently loses data.
    """
    settings = get_settings()
    if not allow_live and settings.account != "leadiq" and settings.prospect != "leadiq":
        raise LeadIQError("LeadIQ API requests are disabled with mock providers.", "disabled")
    key = leadiq_api_key()
    if not key:
        raise LeadIQError("Missing LeadIQ credentials. Copy .env.example to .env and set "
                          "LEADIQ_API_KEY to your Secret Base64 API key.", "missing_key")
    try:
        response = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Basic {key}", "Content-Type": "application/json"},
            json={"query": query, "variables": variables or {}},
            timeout=(10, 30),
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise LeadIQError("LeadIQ request timed out. Try again later.", "timeout") from None
    except requests.ConnectionError as exc:
        raise LeadIQError("Cannot connect to LeadIQ. Check your internet connection and DNS.", "connection") from None
    except requests.RequestException as exc:
        raise LeadIQError("Could not send the LeadIQ request. Check your connection and configuration.") from None

    if response.status_code in (401, 403):
        raise LeadIQError("LeadIQ authentication or access failed. Check your Secret Base64 "
                          "API key and your account's API permissions.", "authentication")
    if response.status_code == 429:
        retry = response.headers.get("Retry-After")
        raise LeadIQError("LeadIQ rate limit reached. " +
                          (f"Retry after {retry}." if retry else "Wait before trying again."), "rate_limit")
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
        code = "graphql"
        if any(term in classification for term in ("rate limit", "rate_limit", "too_many_requests", "throttl")):
            code = "rate_limit"
            hint = "Wait before retrying; check your LeadIQ request limits."
        elif any(term in classification for term in ("unauth", "forbidden", "permission", "api key")):
            code = "authentication"
            hint = "Check your API key and account permissions."
        elif any(term in classification for term in ("credit", "quota", "payment required")):
            code = "credits"
            hint = "LeadIQ reports insufficient credits or quota. Check your plan before retrying."
        elif any(term in classification for term in ("cannot query", "unknown", "not defined", "validation")):
            code = "schema"
            hint = "Your schema differs from the public reference. Update the queries " \
                   "and input mappings in leadiq_schema.py " \
                   "against your LeadIQ schema; no fallback fields are guessed."
        else:
            hint = "Check your LeadIQ API access, credits and query parameters."
        raise LeadIQError(f"LeadIQ GraphQL error: {detail}. {hint}", code)
    if not response.ok:
        raise LeadIQError(f"LeadIQ HTTP error {response.status_code}. "
                          "Check API access and parameters; retry later for server errors.")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise LeadIQError("LeadIQ returned an unexpected response: missing GraphQL data object.")
    return payload["data"]


def test_auth(*, allow_live=False):
    """Verify access with the documented, small account query."""
    data = leadiq_request(AUTH_QUERY, allow_live=allow_live)
    if not isinstance(data.get("account"), dict):
        raise LeadIQError("LeadIQ returned no account. Verify API access and the account query schema.")
    return data["account"]


def search_companies(country=None, min_employees=None, max_employees=None,
                     industry=None, limit=20, *, client=None):
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
    request = client.request if client else leadiq_request
    data = request(COMPANY_SEARCH_QUERY, {"input": {"companyFilter": filters, "limit": limit}})
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


class LeadIQClient:
    """Session-scoped gateway. Cache payloads only; never keys or auth headers."""
    def __init__(self, ttl_seconds=3600):
        self.cache = ResponseCache(ttl_seconds)
        self._credential_scope = None
        self.status = "Configured — not tested"

    def request(self, query, variables=None):
        key = leadiq_api_key()
        # Only an irreversible fingerprint scopes the cache across credential
        # rotation. The raw credential is used transiently by the HTTP transport.
        scope = hashlib.sha256(key.encode()).digest() if key else None
        if scope != self._credential_scope:
            self.cache.clear()
            self._credential_scope = scope
        try:
            if not key:
                raise LeadIQError("LeadIQ not configured. Set LEADIQ_API_KEY or choose mock providers.", "missing_key")
            value = self.cache.get_or_load(query, variables,
                lambda: leadiq_request(query, variables, allow_live=True))
            self.status = "Connected"
            return value
        except LeadIQError as exc:
            self.status = "Not configured" if exc.code == "missing_key" else "Error: " + exc.code.replace("_", " ")
            # Log categories only: no headers, credentials, raw responses or PII.
            logging.getLogger(__name__).warning("LeadIQ request failed (%s)", exc.code)
            raise
