# Account Finder

A local SDR scrubbing workspace for macOS. Discover companies from mock data or
read-only LeadIQ searches, score accounts, rank people, and review a persistent
daily list. The Streamlit workflow does not use Salesforce.

## macOS setup

Check Python (3.10 or newer):

```sh
cd /Users/minghi/thebetterabhiBOT
python3 --version
```

If Python is missing, install Homebrew from [brew.sh](https://brew.sh), then:

```sh
brew install python
```

Create and activate the virtual environment, then install dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

Copy the example only if you do not already have a configuration:

```sh
if [ ! -f .env ]; then cp .env.example .env; fi
```

Open `.env` in VS Code. A working demo needs no API key:

```dotenv
ACCOUNT_PROVIDER=mock
PROSPECT_PROVIDER=mock
SALESFORCE_PROVIDER=disabled
LEADIQ_API_KEY=
LOCAL_DB_PATH=data/app.db
```

Select `.venv/bin/python` as the VS Code interpreter. Activate the environment
again in each new terminal; `deactivate` leaves it.

Start the app with explicit mock overrides so an older live configuration cannot
take precedence:

```sh
cd /Users/minghi/thebetterabhiBOT
source .venv/bin/activate
ACCOUNT_PROVIDER=mock PROSPECT_PROVIDER=mock USE_MOCK_DATA=true python3 -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Open [Account Finder](http://127.0.0.1:8501/). Stop with Ctrl+C. If `.env` already
selects mock providers, the shorter requested command also works:

```sh
USE_MOCK_DATA=true python3 -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

Explicit `ACCOUNT_PROVIDER` / `PROSPECT_PROVIDER` settings override the legacy
`USE_MOCK_DATA` defaults; environment variables override `.env`. The UI ignores
Salesforce configuration, including invalid or missing CRM settings. Existing
Salesforce adapters are retained only for the separate CLI comparison commands.

## Local Daily Scrub Lists

The database is the source of truth for account lists and review statuses.

- Default database: **`/Users/minghi/thebetterabhiBOT/data/app.db`**.
  The folder and schema are created automatically.
- One session exists per **Mac local calendar day**, using the machine's timezone.
  Startup/reruns load today's existing session or create it. UTC timestamps record
  changes; the session's date determines which list owns an account.
- Each Find Accounts search appends eligible companies to today's list. Restarting
  Streamlit or reopening the browser retains accounts, status and saved prospects.
  A new day gets a separate list; History opens previous days for review.
- Account statuses are only **NEW**, **REVIEWED**, and **REJECTED**. Status changes
  save immediately. Repeated searches do not reset status or overwrite snapshots.
- Nothing is written to LeadIQ or Salesforce. No LeadIQ lists are created.
- Mock and live account sources are labeled `mock` and `leadiq`; prospect source
  is stored separately. The fixtures contain 44 fictional companies and 351 people.

Deduplication is scoped to a single day: normalized domain first, then
provider-qualified company ID, then exact normalized company name when stronger
identifiers are missing. Domains strip HTTP/HTTPS, `www.`, ports, paths and trailing
dots, lowercase and handle IDNs; other subdomains remain distinct. Names normalize
case, accents, punctuation and trailing legal suffixes. No fuzzy matching is used
for local scrub lists. Two companies with the same name but conflicting strong
identifiers stay separate. An ID is namespaced by provider; a domain can match
across providers. Alternate strong identities from duplicate searches are remembered.

Example in the current fixtures (UK, 500–5000 employees, minimum Account Score 60):
Manufacturing adds 9, Logistics adds 4, repeating Manufacturing skips 9 duplicates.
Today's list contains 13 accounts. The same company can appear on another day.

A bounded snapshot of up to 10 ranked people supports offline review; the panel
shows the top three. The app stores account summary fields and minimal prospect
identity/title/score/link fields, not full provider payloads, email addresses,
phone numbers or credentials. Provider search caches are separate, in-memory only.

Override `LOCAL_DB_PATH` if needed; relative paths resolve against this project.
The database and SQLite sidecars are ignored by Git at the default location.
If you choose another location, keep that database outside version control yourself.

## Workspace

Sidebar navigation:

| Page | Working surface |
| --- | --- |
| Account Discovery | Compact filters and Find Accounts; the daily table remains below |
| Today's Scrub List | Accounts/New/Reviewed/Rejected metrics, dense sortable table, review panel |
| History | Date selector, that day's metrics/table and review panel |

The wide layout uses text branding, navy text, white surfaces, thin gray borders
and a small green primary action. No NinjaOne logos or downloaded assets are used.
The light theme is configured in `.streamlit/config.toml`; compact style overrides
are centralized in `app.py`.

Find Accounts performs account retrieval → local filters and Account Score →
people retrieval/ranking for new companies → transactional daily append. A summary
reports candidates, additions, duplicates, below-threshold companies and eligible
companies beyond the result limit, then focuses Today's Scrub List. No competing
permanent results table is maintained. Already-listed accounts skip repeated people
searches. Rendering, changing page and reviewing saved records never trigger API calls.

Primary filters are country (All/UK/Ireland), industry, inclusive employee bounds,
minimum Account Score, persona, comma-separated job titles, seniority, minimum
Prospect Score and people per account. More filters contains result limit,
function and skill groups. Live industry accepts a custom API-supported value.
Unknown employee counts fail a specified employee bound.

Table columns: Score, Company, Employees, Industry, Country, Best Prospect,
Prospect Score, Status, Actions. Default order is Account Score descending, then
company name and local ID. Sort through column headings; filter by status.
**Select a row to Review**, or use the Review picker below the table; Actions
contains Open Sales Nav. The review panel separates Account Score from Prospect
Score, shows account information/top three people, and saves status immediately.

Find Better Prospects searches the **same company** and replaces its saved ranked
prospect snapshot without changing Account Score/status. Refine prospect search
provides optional persona, titles and minimum score; blank selections broaden the
search across personas, with top three saved. Select the matching prospect provider
in Data sources when reviewing a record from another source. A company is retained
even if no person matches or a people request fails; failures stop further people
requests for that batch and show a clear message.

Sales Navigator uses an existing safe HTTPS LinkedIn profile/lead URL when present.
Otherwise the existing helper builds a best-effort people search using name/company.
Manual copyable search text is available in review. The user opens/signs in manually;
there is no scraping, login automation, browser automation or LinkedIn API integration.

## Database safety and development reset

`database.py` owns initialization and transactional schema migrations;
`repositories.py` owns queries. SQLite constraints enforce unique days,
daily strong identities, fallback keys, valid statuses/sources and score bounds.
Serialized transactions prevent concurrent duplicate inserts and roll back failed
batches. Connections enable foreign keys and a 10-second lock timeout.

Schema version 1 uses `PRAGMA user_version` and an application identifier. Newer
schemas and unrecognized existing databases are rejected with data preserved.
Future migrations must be explicit, incremental and transactional. No automatic
destructive migration or reset occurs.

**Stop Streamlit before resetting.** This permanently removes all local daily lists:

```sh
python3 main.py reset-local-data
```

Type the exact word `RESET` to confirm; any other answer or EOF cancels. To confirm
without a prompt, for disposable development data:

```sh
python3 main.py reset-local-data --yes
```

This operates only on `LOCAL_DB_PATH` (default `data/app.db`) and its SQLite
sidecars. It refuses unrelated files/symlinks, does not remove provider fixtures or
`.env`, and makes no API calls. The app initializes a fresh database next startup.
To back up local lists, stop Streamlit and copy `data/app.db` to a safe location.

## Read-only LeadIQ

Set `ACCOUNT_PROVIDER=leadiq` and `PROSPECT_PROVIDER=leadiq` in `.env`.
Put the **Secret Base64 API key** from LeadIQ Settings → API Keys in
`LEADIQ_API_KEY`. Do not encode it again or include the `Basic` prefix.
Never commit `.env`. Start using the same Streamlit command without mock overrides.

```sh
python3 main.py test-auth --provider leadiq
```

Authentication uses `query TestAuth { account { plans { name } } }` against
`https://api.leadiq.com/graphql`. Missing keys/placeholders, authentication,
rate limits, connection/timeouts, HTTP/JSON and GraphQL errors produce readable
messages. The app installs and starts without a key and offers Use mock LeadIQ.
Live requests are explicit user actions only; Find Accounts includes people
searches for newly found companies. These searches may consume plan credits.

Queries are isolated in `leadiq_schema.py`, based on the
[LeadIQ reference](https://developer.leadiq.com/) and
[public API guide](https://leadiqhelp.zendesk.com/hc/en-us/articles/29375289152795-LeadIQ-Public-API-Guide).
No authenticated live validation has been performed because no real key is available.
Schema errors point to the query/input mappings; fields are never invented.

Verified company fields: ID/name/domain/employees/industry/country/city and
`totalContactsInCompany`. Company LinkedIn URL, IT headcount, locations, IT hiring
and growth remain `None`. **Current live Account Scores can reach at most 55**;
the live minimum defaults to 0. Scoring weights are unchanged.
Flat people search supplies ID/company ID/name/title/role/seniority/location/profile
URL; skills are `[]`, email/phone availability unknown, with no enrichment.
The saved snapshot omits those unavailable fields.

LeadIQ Executive maps to local C-Level, SeniorIndividualContributor to Senior,
and InformationTechnology/IT to Information Technology; unknown values remain
unchanged. Head/Entry are filtered locally because they are not documented API
filter enums. Title search and country/industry values depend on the API schema/plan.
Ranking is over one bounded retrieved page, not every possible API result.
No automatic pagination, retries or enrichment is included.

Configuration:

```dotenv
LEADIQ_CACHE_TTL_SECONDS=3600
LEADIQ_ACCOUNT_FETCH_LIMIT=100
LEADIQ_PROSPECT_FETCH_LIMIT=50
```

Company budget accepts 1–500; people budget 1–100. Successful GraphQL responses
use a per-session bounded TTL cache (0–86400 seconds; 0 disables). Errors are
not cached. Keys/raw Authorization headers are never stored; rotating credentials
invalidates cached results. Connected means a request succeeded in this session,
not an ongoing health guarantee.

## Scoring and architecture

Account and Prospect Scores remain independent deterministic 0–100 values.
Account weights: size 25, industry 20, UK/Ireland 10, IT headcount 15,
locations 10, IT hiring 10, growth 10. Missing signals earn zero.
Account display bands: Exceptional ≥90, Strong ≥80, Good ≥70, Secondary ≥55,
Weak below 55; these labels do not change scoring.

Prospect weights: title 30, seniority 20, function 15, relevant skills 20,
location 5, decision influence 10, with existing role tiers/penalties and
stable tie-breaks. Persona/title/seniority/function filters combine with AND;
multiple titles use OR; Custom requires a title. Skill groups supply ranking
evidence and do not require a person to have listed skills.
See [prospect_calibration_report.md](prospect_calibration_report.md) for the
existing 351-person calibration. These are SDR assumptions, not predictive models.

```text
app.py → scrub_service.py
          ├─ providers / leadiq.py → discovery.py → scoring.py
          ├─ prospect providers → prospect_discovery.py → prospect_scoring.py
          └─ repositories.py → database.py → data/app.db
main.py → existing isolated CLI services + local reset command
```

The separate CLI comparison path and `matching.py`/Salesforce CSV/mock adapters
remain for later use. They are not required by or read from the daily UI.
CLI searches print results and **do not append** to the daily database:

```sh
python3 main.py search --provider mock --salesforce-provider disabled --country "United Kingdom" --industry "Manufacturing" --min-employees 500 --max-employees 5000 --limit 20
python3 main.py prospects --provider mock --company-id MOCK-LIQ-025 --limit 3
python3 main.py --help
```

CSV comparison is optional through `search --salesforce-provider csv
--salesforce-csv-path /path/to/export.csv`; required UTF-8 headers are
`salesforce_id,account_name,domain,country`. Legacy CLI statuses describe that
explicit comparison only; no CRM verification is claimed in the daily app.

## Tests

```sh
source .venv/bin/activate
python3 -m pytest -q
python3 -m compileall -q app.py database.py repositories.py scrub_service.py main.py config.py tests
```

Tests isolate `.env`, credentials and database paths. Mock flows make no HTTP
requests; live-provider tests use synthetic responses. Coverage includes original
normalization/scoring/prospect calibration, migrations, transactions/concurrency,
daily reuse/rollover, domain/ID/name deduplication, status persistence, ordering,
three searches, restart, history, missing keys and confirmation-protected reset.
UI tests use Streamlit AppTest; history validation uses a separate previous-day
test session. No Salesforce API, writeback, LLM, cloud/team features or notifications
are part of this iteration.
