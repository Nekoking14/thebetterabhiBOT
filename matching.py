"""Conservative Salesforce matching independent of data providers."""
import re
import unicodedata
from difflib import SequenceMatcher

from leadiq import normalize_domain

NAME_SIMILARITY_THRESHOLD = 90


def normalize_company_name(name):
    value = unicodedata.normalize("NFKD", name or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).casefold()
    value = re.sub(r"[^\w\s]", " ", value.replace("&", " and "))
    words = value.split()
    while words and words[-1] in {"limited", "ltd", "plc", "llc", "inc", "incorporated", "corporation", "corp"}:
        words.pop()
    return " ".join(words)


def company_name_similarity(left, right):
    left, right = normalize_company_name(left), normalize_company_name(right)
    return 100 * SequenceMatcher(None, left, right, autojunk=False).ratio() if left and right else 0


def exact_domain_match(left, right):
    left, right = normalize_domain(left), normalize_domain(right)
    return bool(left and right and left == right)


def normalize_country(country):
    value = (country or "").strip().casefold()
    return {"uk": "united kingdom", "gb": "united kingdom", "ie": "ireland"}.get(value, value)


def detect_salesforce_duplicate(company, accounts):
    """Return exactly EXISTS, POSSIBLE_MATCH or NET_NEW; exact domains win.

    Strong name similarity requires the same known country. Missing geography
    cannot support a fuzzy match. A fuzzy name never proves an existing account.
    """
    accounts = list(accounts)
    for account in accounts:
        if exact_domain_match(company.get("domain"), account.get("domain")):
            return "EXISTS"
    country = normalize_country(company.get("country"))
    for account in accounts:
        if (country and country == normalize_country(account.get("country"))
                and company_name_similarity(company.get("company_name"), account.get("account_name"))
                >= NAME_SIMILARITY_THRESHOLD):
            return "POSSIBLE_MATCH"
    return "NET_NEW"
