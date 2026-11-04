# Overview

NAARI measures women's empowerment, agency and resilience in agriculture. Programme teams build surveys here, field teams collect responses with the [field app](https://github.com/intellecap-weia-fpo/naari-mobile) or the web form, and the platform scores indicators and reports the results.

## Use cases

- **Survey manager:** builds a survey, its consent text, translations and indicators, then assigns it to users.
- **Enumerator:** collects responses in the field, offline if needed, and syncs them later.
- **Administrator or analyst:** reviews responses, sees indicator results and dashboards, and exports data.
- **Organisation:** runs its own workspace, with its data kept in a separate database schema.

## Architecture

| Part | Role |
|---|---|
| `survey/` | Surveys, questions, responses, indicators, reports, exports, translations |
| `customers/` | Workspaces (tenants), using django-tenants schemas |
| `auth_app/`, `management/` | Accounts, roles and user management |
| `neo/` | Shared site configuration, language and email helpers |
| `one/` | Django settings, URLs and the combined GraphQL schema |

It is a Django application with a Graphene GraphQL API at `/graphql/`, PostgreSQL, and Redis with Celery for background exports. Configuration comes from the environment; see `.env.example`.

Indicators are computed by scripts stored per survey. They run in a restricted, resource-limited sandbox (`survey/models.py`, `survey/test_indicator_sandbox.py`).

The repository ships one [sample questionnaire](../samples/README.md). Each deployment loads its own questionnaires and indicators.

## Getting data out

- **XLSX and CSV:** the survey's submissions, from its Import/Export page (`<id>/import-export/`) and `submissions/export/`.
- **XLSX reports:** `report/export/`.
- **GraphQL:** `/graphql/`.

Exports contain whatever a survey collected, so a team sharing data more widely should remove personal columns first.
