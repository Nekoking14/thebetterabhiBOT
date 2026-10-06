"""Provider-facing prospect contract and validation. No source-specific fields."""
from typing import TypedDict


class ProspectRecord(TypedDict):
    prospect_id: str
    company_id: str
    first_name: str | None
    last_name: str | None
    full_name: str | None
    job_title: str | None
    seniority: str | None
    function: str | None
    country: str | None
    city: str | None
    linkedin_url: str | None
    skills: list[str]
    email_available: bool | None
    phone_available: bool | None


def validate_prospect(record):
    if not isinstance(record, dict):
        raise ValueError("Prospect records must be objects.")
    text_fields = ("prospect_id", "company_id", "first_name", "last_name", "full_name",
                   "job_title", "seniority", "function", "country", "city")
    if any(record.get(key) is not None and not isinstance(record[key], str) for key in text_fields):
        raise ValueError("Prospect identity, title and location fields must be strings or null.")
    if not record.get("prospect_id") or not record.get("company_id"):
        raise ValueError("Prospect and company IDs cannot be empty.")
    if not isinstance(record.get("skills"), list) or any(not isinstance(s, str) for s in record["skills"]):
        raise ValueError("Prospect skills must be a list of strings.")
    if any(record.get(key) is not None and not isinstance(record[key], bool) for key in ("email_available", "phone_available")):
        raise ValueError("Email and phone availability must be boolean flags or null (unknown).")
    if record.get("linkedin_url") is not None and not isinstance(record["linkedin_url"], str):
        raise ValueError("LinkedIn URL must be a string or null.")
    return {**record, "skills": list(record["skills"])}
