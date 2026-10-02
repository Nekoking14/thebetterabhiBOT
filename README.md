# SDR account discovery MVP

Local Python CLI with fictional LeadIQ candidates and Salesforce accounts. It
matches duplicates, defaults to NET_NEW accounts, scores ICP fit and ranks results.
No Streamlit, Salesforce API, AI, scraping or Sales Navigator automation is included.

## Running without API keys

On macOS, open the project in VS Code and use its terminal. Check Python first:

```sh
cd /Users/minghi/thebetterabhiBOT
python3 --version
```

If Python is missing, install Homebrew from [brew.sh](https://brew.sh), then run:

```sh
brew install python
```

For a fresh setup:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```

Create your environment file if it does not already exist (preserves existing keys):

```sh
if [ ! -f .env ]; then cp .env.example .env; fi
```

Open `.env` in VS Code and ensure it contains:

```dotenv
USE_MOCK_DATA=true
LEADIQ_API_KEY=your_leadiq_api_key_here
```

No real key is needed. Mock mode is also the default when the variable is absent.
Only `true` and `false` are accepted (case-insensitive). Environment variables take
precedence over `.env`; the file is loaded relative to the project directory.
`.env` and `.venv` are ignored by Git. Select `.venv/bin/python` in VS Code.

Run a ranked search:

```sh
USE_MOCK_DATA=true python3 main.py search --country "United Kingdom" --industry "Manufacturing" --min-employees 500 --max-employees 5000 --min-score 60 --limit 5
```

Explore all candidates and duplicate statuses:

```sh
USE_MOCK_DATA=true python3 main.py search --show-all --limit 50
USE_MOCK_DATA=true python3 main.py search --country "Ireland" --min-score 50 --limit 10
python3 main.py --help
```

Activate the environment each time you open a terminal with
`source .venv/bin/activate`; use `deactivate` when finished.

All 44 companies are fictional. Domains use the reserved `.example` namespace;
none is presented as real LeadIQ data. The 20 mock Salesforce rows intentionally
produce 16 exact domain duplicates and 4 possible name matches, leaving 24 net-new
accounts before filters. The fixtures include UK/Ireland, different cities,
industries, headcounts, locations and growth/hiring signals.

## Architecture

```text
Mock LeadIQ JSON / LeadIQ API adapter
                  ↓
        canonical account candidates
                  ↓
Salesforce matching ← mock CSV / future Salesforce API adapter
                  ↓
          NET_NEW (default filter)
                  ↓
       ICP scoring → ranked results → CLI
```

- `config.py`: loads `.env` and chooses mock or live mode.
- `providers.py`: source adapters. Candidate dictionaries use `company_id`,
  `company_name`, `domain`, `employee_count`, `industry`, `country`, `city`,
  `matching_contacts`, `it_headcount`, `number_of_locations`, `it_hiring` and
  `growth_signal`. CRM dictionaries use `salesforce_id`, `account_name`, `domain`
  and `country`. Business logic depends on these records rather than file/API access.
- `leadiq.py`: preserved GraphQL client, authentication test, company search and
  shared domain normalization. Direct API requests are blocked in mock mode.
- `matching.py`: name normalization, similarity, exact domain comparison and
  duplicate status. `normalize_domain` is re-exported here from the shared implementation.
- `scoring.py`: weights, target industries and tier rules in one place.
- `discovery.py`: pure candidate filtering, duplicate classification, scoring,
  score/status filtering, sorting and final result limiting.
- `main.py`: CLI parsing, presentation and human-readable errors.

`--country` and `--industry` are case-insensitive exact filters; UK/GB and IE are
country aliases. Employee bounds are inclusive. Unknown employee counts fail a
specified employee bound. `--min-score` accepts 0–100, `--limit` is positive and
applies after matching, score filtering and ranking. Default display is NET_NEW;
`--show-all` includes POSSIBLE_MATCH and EXISTS. All displayed accounts sort by
score descending, then company name and ID for deterministic ties.

The summary counts candidates after country/industry/employee filters, **before**
status, minimum-score and display-limit filters. Thus it describes Salesforce
coverage of the search pool. The display count separately shows eligible and
printed accounts. In mock mode the entire fixture is processed before limiting.

## Duplicate policy

Exact normalized domain equality always returns EXISTS, regardless of name or
country. A missing domain never matches another missing domain. Domain normalization
removes HTTP/HTTPS, leading `www.`, ports, paths and trailing dots, handles IDNs and
lowercases hostnames. Other subdomains are preserved; it does not derive registrable
domains, follow redirects or merge corporate subsidiaries.

Company names are case-folded, accents/punctuation normalized and trailing legal
suffixes such as Limited/Ltd/PLC removed. Standard-library `difflib.SequenceMatcher`
returns similarity from 0–100. A similarity of at least 90 **and the same known
country** yields POSSIBLE_MATCH. Missing or conflicting geography cannot support
this name-only comparison. Fuzzy names never yield EXISTS; possible matches are
excluded by default for review. Country is the supporting geography because the
mock CRM contract contains no city. Threshold and suffix policy are editable in
`matching.py`; these heuristic decisions require calibration on real CRM data.

## Editable ICP model

| Component | Points / rule |
| --- | --- |
| Employee size | 25 for 500–5000 inclusive, otherwise 0 |
| Industry | 20 for Manufacturing, Technology or Logistics |
| UK/Ireland | 10 |
| IT headcount | 15 for ≥50, 10 for ≥20, 5 for ≥5 |
| Locations | 10 for ≥5, 5 for ≥2 |
| IT hiring | 10 for boolean true |
| Growth signal | 10 for boolean true |

Maximum is 100. `WEIGHTS` must total 100; edit weights and tiers in `scoring.py`.
The function returns `total_score`, `components` and a reason for each awarded
component. Unknown signals earn zero. These are initial product assumptions,
not a validated predictive model; fictional boolean hiring/growth flags simulate
signals that a later provider will need to supply.

## Live LeadIQ foundation

LeadIQ queries follow the [public schema](https://developer.leadiq.com/) and
[authentication guide](https://leadiqhelp.zendesk.com/hc/en-us/articles/29375289152795-LeadIQ-Public-API-Guide).
Use the **Secret Base64 API key** from LeadIQ Settings > API Keys in `.env`. Do not
encode it again or prefix the stored value with `Basic`. To test credentials:

```sh
USE_MOCK_DATA=false python3 main.py test-auth
```

Without a key this shows a clear error. In mock mode `test-auth` explicitly skips
verification and makes no request. The live query is
`query TestAuth { account { plans { name } } }`.

`leadiq_request(query, variables=None)` returns GraphQL data or raises `LeadIQError`.
It handles authentication, rate limits, connection/timeouts, HTTP/JSON failures and
GraphQL errors, rejecting partial responses. It uses 10-second connection and
30-second read timeouts without automatic retries. Schema validation errors direct
you to update the query/input mappings against your account schema.

The documented grouped advanced search supports locations/country, industries,
company-size min/max and company limit. It returns company ID/name/domain/employee
count/industry/country/city and `totalContactsInCompany`. This count is not a count
of verified emails. The returned company type has no LinkedIn URL; `linkedin_url`
remains `None`. IT headcount, locations and hiring/growth fields are not requested
from the API or invented; the adapter sets them to `None` and scoring gives no
points for those signals. Live verification remains pending actual credentials.

Switching to `USE_MOCK_DATA=false` selects live adapters without changing matching
or scoring, but **live discovery is intentionally unavailable until the Salesforce
API adapter is implemented**. It fails clearly before contacting LeadIQ and does
not silently compare live prospects against fictional CRM data. The LeadIQ adapter
currently supplies one bounded page of 100 candidates; production discovery will
need filter pushdown and pagination before ranking an entire live search pool.

## Tests

```sh
source .venv/bin/activate
python3 -m pytest -q
python3 -m compileall -q main.py leadiq.py config.py providers.py matching.py scoring.py discovery.py tests
```

Tests cover normalization, domain priority, fuzzy-match geography, scoring and
boundaries, filtering, summary semantics, ranking, fixture counts, missing keys,
API error handling and mock-mode network isolation. Requests are mocked; tests
never need credentials or consume credits. Original unittest tests also run under
pytest.
