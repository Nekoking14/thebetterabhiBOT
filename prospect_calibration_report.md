# Prospect scoring calibration — October 6, 2026

All 351 prospects are fictional. This report uses the existing, unchanged fixtures,
all personas and skill groups, and no minimum score. Account scoring is unchanged.
The model is deterministic; the distribution is evidence from demo data, not real buyer validation.

## Distribution

| Band | Score | Count | Share |
| --- | --- | ---: | ---: |
| Exceptional | 90–100 | 66 | 18.8% |
| Strong | 80–89 | 57 | 16.2% |
| Good | 70–79 | 38 | 10.8% |
| Secondary | 55–69 | 88 | 25.1% |
| Weak | 0–54 | 102 | 29.1% |

Average: **58.33**. Median: **64**. Minimum: **0**. Maximum: **100**.

90+: **66 / 351 (18.8%)**. Perfect 100: **7 (2.0%)**.

Before calibration: 43.9% scored 90+, 77 scored 100, mean 73.68 and median 85. The recalibrated 90+ share is below the roughly 20% inflation warning. No records were added, removed or edited to force that result.

## Top ten

| ID | Fictional prospect | Title | Company | Score | Band | Tier |
| --- | --- | --- | --- | ---: | --- | ---: |
| MOCK-P-033-02 | Arlen Briarvale | Chief Information Officer | Starfenn Manufacturing Ltd | 100 | Exceptional | 1 |
| MOCK-P-018-02 | Celia Mossfield | Chief Information Officer | Amberquay Technology Limited | 100 | Exceptional | 1 |
| MOCK-P-038-02 | Elara Mossfield | Chief Information Officer | Quartzwick Technology Limited | 100 | Exceptional | 1 |
| MOCK-P-028-02 | Iona Wrenbrook | Chief Information Officer | Pinehaven Engineering Ltd | 100 | Exceptional | 1 |
| MOCK-P-023-02 | Niall Thistleford | Chief Information Officer | Cobaltgrove Logistics Limited | 100 | Exceptional | 1 |
| MOCK-P-003-02 | Nolan Thistleford | Chief Information Officer | Coppermere Logistics Ltd | 100 | Exceptional | 1 |
| MOCK-P-013-02 | Tobin Briarvale | Chief Information Officer | Willowquay Systems Limited | 100 | Exceptional | 1 |
| MOCK-P-009-02 | Arlen Briarvale | VP Information Technology | Birchspire Components Limited | 98 | Exceptional | 1 |
| MOCK-P-014-02 | Elara Mossfield | VP Information Technology | Oakmere Textiles Ltd | 98 | Exceptional | 1 |
| MOCK-P-019-02 | Finley Thistleford | VP Information Technology | Cedarwick Logistics Limited | 98 | Exceptional | 1 |

## Five weak examples

| ID | Fictional prospect | Title | Company | Score | Main deduction |
| --- | --- | --- | --- | ---: | --- |
| MOCK-P-010-01 | Arlen Quaybourne | IT Support Specialist | Larkstone Digital Ltd | 22 | −10: Support specialist/analyst role has limited buying authority |
| MOCK-P-012-09 | Finley Elmfirth | Sales Director | Thistlewick Retail Ltd | 0 | −40: Sales, marketing, HR, finance or recruiting role |
| MOCK-P-005-06 | Arlen Elmfirth | Marketing Manager | Wrenhaven Engineering Ltd | 0 | −40: Sales, marketing, HR, finance or recruiting role |
| MOCK-P-002-06 | Celia Rowancrest | Finance Manager | Alderquay Technology Limited | 0 | −40: Sales, marketing, HR, finance or recruiting role |
| MOCK-P-022-01 | Arlen Quaybourne | IT Support Specialist | Harborfern Technology Ltd | 22 | −10: Support specialist/analyst role has limited buying authority |

## Senior decision maker versus junior technical person

Synthetic test scenarios, not fixture edits. All have UK location and IT function.

| Role | Seniority | Skills | Score | Band | Tier |
| --- | --- | --- | ---: | --- | ---: |
| Head of IT | Head | None listed | 76 | Good | 1 |
| Junior IT Intern | Entry | Intune, PowerShell, Active Directory, Endpoint Management, Patch Management, RMM | 22 | Weak | 4 |
| IT Support Specialist | Individual Contributor | Intune, PowerShell, Active Directory, Endpoint Management, Patch Management, RMM | 40 | Weak | 4 |

Skills do not override poor authority. Head of IT without skills keeps 76 points; the junior and support roles remain weak even with full skill points.

## Flintquay Manufacturing

Account Score: **100**, unchanged. Initial contact: **Mira Fenquill, IT Support Specialist — 22 (Weak)**. Find Better Prospects keeps the company and returns the top three below.

| Rank | Prospect | Title | Score | Band | Tier |
| ---: | --- | --- | ---: | --- | ---: |
| 1 | Tobin Briarvale | Head of Technology | 96 | Exceptional | 1 |
| 2 | Elara Ashwick | Infrastructure Manager | 81 | Strong | 2 |
| 3 | Nolan Quaybourne | IT Operations Manager | 78 | Good | 2 |
| 4 | Iona Mossfield | Systems Administrator | 64 | Secondary | 3 |
| 5 | Celia Cedarwell | Service Desk Manager | 63 | Secondary | 3 |
| 6 | Mira Fenquill | IT Support Specialist | 22 | Weak | 4 |
| 7 | Orin Elmfirth | Marketing Manager | 0 | Weak | 4 |

## Explanation and reproducibility

Configuration: `prospect_scoring.py` weights, penalties, relevance units and specialty overrides; `personas.py` explicit authority tiers and influence rules. Full skill points require relevant depth and breadth, not just a large skill list. Penalties are subtracted from positive components before clamping. Missing skills are not a penalty.

Run `python3 -m pytest -q` for all existing and calibration regressions. Run `USE_MOCK_DATA=true python3 main.py prospects --company-id MOCK-LIQ-025` to reproduce the top three Flintquay prospects.
