"""Outbound link helpers only. No requests, scraping or automatic sign-in."""
from urllib.parse import urlencode, urlsplit


def sales_navigator_link(prospect, company_name):
    url = prospect.get("linkedin_url")
    if url:
        try:
            parsed = urlsplit(url)
            if (parsed.scheme == "https" and parsed.hostname in {"linkedin.com", "www.linkedin.com"}
                    and not parsed.username and not parsed.password
                    and parsed.path.startswith(("/in/", "/sales/lead/"))):
                return {"url": url, "search_text": None, "is_search": False}
        except ValueError:
            pass
    text = f"{(prospect.get('full_name') or '').strip()} {(company_name or '').strip()}".strip()
    # Best-effort keyword prefill. LinkedIn does not document a stable deep-link
    # contract; always show copyable search text as a manual fallback.
    return {"url": "https://www.linkedin.com/sales/search/people?" + urlencode({"keywords": text}),
            "search_text": text, "is_search": True}
