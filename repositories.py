"""Daily scrub lists and identities; all SQL stays inside the persistence layer."""
import json
from datetime import date, datetime, timezone

from database import connection, initialize_database
from leadiq import normalize_domain
from matching import normalize_company_name

STATUSES = ("NEW", "REVIEWED", "REJECTED")


def local_today():
    return datetime.now().astimezone().date()


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def account_score_band(score):
    # Presentation bands only; the existing account scoring model is unchanged.
    return next(label for minimum, label in
                ((90, "Exceptional"), (80, "Strong"), (70, "Good"), (55, "Secondary"), (0, "Weak"))
                if score >= minimum)


def identities(account):
    domain = normalize_domain(account.get("domain"))
    company_id = str(account.get("company_id") or "").strip()
    keys = []
    if domain:
        keys.append(("domain", domain))
    if company_id:
        keys.append(("provider", json.dumps([account["source"], company_id])))
    return domain, company_id or None, keys


def prospect_snapshot(ranked):
    """Bounded review snapshot: no raw provider payloads or contact details."""
    fields = ("prospect_id", "company_id", "full_name", "job_title", "linkedin_url", "source")
    people = []
    for person in ranked.get("results", [])[:10]:
        score = person["prospect_score"]
        people.append({**{key: person.get(key) for key in fields},
                       "prospect_score": {"total_score": score["total_score"], "score_band": score["score_band"]}})
    best = people[0] if people else {}
    return (best.get("full_name"), best.get("job_title"),
            best.get("prospect_score", {}).get("total_score"),
            ranked.get("eligible_count", len(people)), json.dumps(people))


class ScrubRepository:
    def __init__(self, path=None):
        self.path = initialize_database(path)

    def ensure_session(self, day=None):
        day = day or local_today()
        day = date.fromisoformat(str(day)).isoformat()
        now = timestamp()
        with connection(self.path) as db, db:
            db.execute("INSERT INTO scrub_sessions(session_date,created_at,updated_at) VALUES(?,?,?) "
                       "ON CONFLICT(session_date) DO NOTHING", (day, now, now))
            return dict(db.execute("SELECT * FROM scrub_sessions WHERE session_date=?", (day,)).fetchone())

    def sessions(self):
        with connection(self.path) as db:
            return [dict(row) for row in db.execute(
                "SELECT s.*,COUNT(a.id) AS account_count FROM scrub_sessions s LEFT JOIN scrub_accounts a "
                "ON a.session_id=s.session_id GROUP BY s.session_id ORDER BY s.session_date DESC")]

    def accounts(self, session_id):
        with connection(self.path) as db:
            return [self._account(row) for row in db.execute(
                "SELECT * FROM scrub_accounts WHERE session_id=? "
                "ORDER BY account_score DESC, company_name COLLATE NOCASE, id", (session_id,))]

    @staticmethod
    def _account(row):
        account = dict(row)
        account["prospects"] = json.loads(account.pop("prospects_json"))
        return account

    @staticmethod
    def _duplicate(db, session_id, account):
        _, _, keys = identities(account)
        for kind, value in keys:  # Domain wins, then provider-qualified ID.
            found = db.execute("SELECT account_id FROM account_identities WHERE session_id=? AND kind=? AND value=?",
                               (session_id, kind, value)).fetchone()
            if found:
                return found[0]
        name = normalize_company_name(account.get("company_name"))
        if name:
            # Name-only incoming records may match a known company. Strong records
            # only match name-only snapshots; conflicting strong identities stay distinct.
            sql = "SELECT id FROM scrub_accounts WHERE session_id=? AND name_key=?"
            if keys:
                sql += " AND fallback_key IS NOT NULL"
            found = db.execute(sql + " ORDER BY id LIMIT 1", (session_id, name)).fetchone()
            if found:
                return found[0]
        return None

    def contains(self, session_id, account):
        with connection(self.path) as db:
            return self._duplicate(db, session_id, account) is not None

    def append(self, session_id, accounts):
        added = duplicates = 0
        now = timestamp()
        with connection(self.path) as db, db:
            # Identity lookup + insert are atomic even across concurrent app sessions.
            db.execute("BEGIN IMMEDIATE")
            for account in accounts:
                domain, company_id, keys = identities(account)
                name = normalize_company_name(account.get("company_name"))
                if not keys and not name:
                    raise ValueError("An account needs a domain, provider ID or company name.")
                existing = self._duplicate(db, session_id, account)
                if existing:
                    duplicates += 1
                    account_id = existing
                    if keys:
                        # Upgrade a weak identity without losing status, scores or
                        # the original snapshot. Never reuse a name-only fallback
                        # after stronger identifiers have become available.
                        db.execute("UPDATE scrub_accounts SET fallback_key=NULL,domain=COALESCE(domain,?),"
                                   "company_id=CASE WHEN source=? THEN COALESCE(company_id,?) ELSE company_id END "
                                   "WHERE id=?", (domain, account['source'], company_id, account_id))
                else:
                    score = account["score"]["total_score"]
                    people = prospect_snapshot(account.get("ranked_prospects", {}))
                    values = (session_id, company_id, account.get("company_name") or "Company name unavailable",
                              domain, name, account.get("country"), account.get("city"), account.get("industry"),
                              account.get("employee_count"), score, account_score_band(score), *people[:3],
                              account["source"], "NEW", now, now, *people[3:],
                              account.get("prospect_source", account["source"]), name if not keys else None)
                    account_id = db.execute(
                        "INSERT INTO scrub_accounts(session_id,company_id,company_name,domain,name_key,country,city,industry,"
                        "employee_count,account_score,account_score_band,best_prospect_name,best_prospect_title,"
                        "best_prospect_score,source,status,date_added,updated_at,matching_prospects,prospects_json,"
                        "prospect_source,fallback_key) VALUES(" + ",".join("?" for _ in values) + ")", values).lastrowid
                    added += 1
                for kind, value in keys:
                    # Remember alternate IDs/domains from duplicate searches. Do not
                    # steal an identity already assigned to another existing account.
                    db.execute("INSERT INTO account_identities VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
                               (session_id, kind, value, account_id))
            if added or duplicates:
                db.execute("UPDATE scrub_sessions SET updated_at=? WHERE session_id=?", (now, session_id))
        return {"added": added, "duplicates": duplicates}

    def set_status(self, account_id, status):
        if status not in STATUSES:
            raise ValueError("Status must be NEW, REVIEWED or REJECTED.")
        with connection(self.path) as db, db:
            now = timestamp()
            row = db.execute("UPDATE scrub_accounts SET status=?,updated_at=? WHERE id=? RETURNING session_id",
                             (status, now, account_id)).fetchone()
            if not row:
                raise ValueError("Scrub account no longer exists.")
            db.execute("UPDATE scrub_sessions SET updated_at=? WHERE session_id=?", (now, row[0]))

    def update_prospects(self, account_id, ranked, source):
        values = prospect_snapshot(ranked)
        with connection(self.path) as db, db:
            now = timestamp()
            row = db.execute("UPDATE scrub_accounts SET best_prospect_name=?,best_prospect_title=?,best_prospect_score=?,"
                             "matching_prospects=?,prospects_json=?,prospect_source=?,updated_at=? WHERE id=? RETURNING session_id",
                             (*values, source, now, account_id)).fetchone()
            if not row:
                raise ValueError("Scrub account no longer exists.")
            db.execute("UPDATE scrub_sessions SET updated_at=? WHERE session_id=?", (now, row[0]))
