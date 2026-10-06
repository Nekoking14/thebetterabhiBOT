"""Interchangeable prospect sources; live implementation is intentionally disabled."""
import json
from typing import Protocol

from config import PROJECT_DIR, get_settings
from leadiq import LeadIQClient, LeadIQError
from leadiq_schema import PROSPECT_SEARCH_QUERY, company_filter, contact_filter, normalize_person
from providers import ProviderError
from prospect_models import ProspectRecord, validate_prospect


class ProspectProvider(Protocol):
    def get_prospects(self, company_id: str, filters=None) -> list[ProspectRecord]: ...


class MockProspectProvider:
    source = "mock"
    def __init__(self, path=None):
        self.path = path or PROJECT_DIR / "mock_prospects.json"
        self._by_company = None

    def get_prospects(self, company_id, filters=None):
        if self._by_company is None:
            try:
                with self.path.open() as source:
                    records = json.load(source)
                if not isinstance(records, list):
                    raise ValueError("Expected a list of prospects")
                by_company, ids = {}, set()
                for record in records:
                    record = validate_prospect(record)
                    if record["prospect_id"] in ids:
                        raise ValueError("Duplicate prospect ID")
                    ids.add(record["prospect_id"])
                    by_company.setdefault(record["company_id"], []).append(record)
                self._by_company = by_company
            except (OSError, ValueError) as exc:
                raise ProviderError(f"Cannot load mock prospects: {exc}") from exc
        return [{**row, "skills": list(row["skills"]), "source": "mock"} for row in self._by_company.get(company_id, [])]


class LeadIQProspectAPIProvider:
    source = "leadiq"

    def __init__(self, client=None, fetch_limit=50):
        self.client = client or LeadIQClient()
        self.fetch_limit = fetch_limit

    def get_prospects(self, company_id, filters=None):
        if not company_id:
            raise ValueError("A company ID is required to find people inside an account.")
        return self.search_prospects(company_id=company_id,
            persona=filters.persona if filters else None, job_titles=filters.job_titles if filters else (),
            seniorities=filters.seniorities if filters else (), functions=filters.functions if filters else ())

    def search_prospects(self, company_id=None, company_name=None, domain=None, persona=None,
                         job_titles=(), seniorities=(), functions=(), country=None, limit=None):
        from prospect_discovery import ProspectFilters, rank_prospects
        limit = self.fetch_limit if limit is None else limit
        if not isinstance(limit, int) or not 1 <= limit <= self.fetch_limit:
            raise ValueError(f"Prospect fetch limit must be 1–{self.fetch_limit}.")
        filters = ProspectFilters(persona=persona, job_titles=tuple(job_titles), seniorities=tuple(seniorities),
                                  functions=tuple(functions), limit=limit)
        criteria = {"companyFilter": company_filter(company_id=company_id, company_name=company_name, domain=domain),
                    "contactFilter": contact_filter(persona, job_titles, seniorities, country), "limit": limit}
        data = self.client.request(PROSPECT_SEARCH_QUERY, {"input": criteria})
        result = data.get("flatAdvancedSearch")
        if not isinstance(result, dict) or not isinstance(result.get("people"), list):
            raise LeadIQError("Unexpected people response. Check leadiq_schema.py against your LeadIQ schema.", "schema")
        records = []
        for person in result["people"][:limit]:
            if not isinstance(person, dict) or not person.get("id") or not person.get("companyId"):
                raise LeadIQError("LeadIQ people response is missing person/company IDs. Check the schema mapping.", "schema")
            # Ignore any row from another employer; never reassign it to this company.
            if company_id and person["companyId"] != company_id:
                continue
            records.append(validate_prospect(normalize_person(person)))
        # Retain canonical raw records, not scores. Use the same local tolerant
        # filters even when the API's search matching semantics differ.
        selected = []
        for cid in dict.fromkeys(r["company_id"] for r in records):
            selected.extend(rank_prospects(records, cid, filters)["results"])
        return [{key: value for key, value in row.items() if key != "prospect_score"} for row in selected]


def get_prospect_provider(settings=None, client=None):
    settings = settings or get_settings()
    return MockProspectProvider() if settings.prospect == "mock" else LeadIQProspectAPIProvider(client, settings.prospect_fetch_limit)
