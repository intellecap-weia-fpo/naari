<p align="center"><img src="docs/logo.png" alt="NAARI logo" width="160"></p>

# NAARI

NAARI (National Assessment of Agency, Resilience and Inclusion) is a survey platform for measuring women's empowerment, agency and resilience in agriculture. It supports the creation and management of surveys, collection of responses, and analysis of indicators related to women's empowerment in agriculture. This repository contains the Django administration and web application; the survey mobile app is a separate component.

## Relevance to the Sustainable Development Goals

- **SDG 5, Gender Equality (targets 5.5 and 5.a):** programmes measure women's decision-making and control over resources, so they can target and track empowerment.
- **SDG 2, Zero Hunger (target 2.3):** the same data covers women food producers in farmer producer organisations and self-help groups.

## Features

- Configure surveys, questions, translations and indicators.
- Manage users and access to surveys.
- Review responses, calculate indicators, and export reports.
- Expose a GraphQL API for supported clients.
- Use optional services for translation, mapping and analytics.

## Requirements

- Python 3.12 or later and [uv](https://docs.astral.sh/uv/).
- PostgreSQL, which is required for the tenant schemas used by this application.
- Redis and a Celery worker for background jobs; optional integrations require their own configuration.

## Local setup

```bash
git clone https://github.com/intellecap-weia-fpo/naari.git
cd naari
uv sync
cp .env.example .env
```

Configure the database, `SECRET_KEY`, `DEBUG`, allowed hosts and any required service credentials in the environment or `.env`. Never commit credentials. Set `DATABASE_URL` to a PostgreSQL connection string, or configure `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST` and `POSTGRES_PORT` individually. See `one/settings.py` and `.env.example` for the available settings.

With PostgreSQL running, apply the tenant-aware migrations and start the development server:

```bash
uv run python manage.py migrate_schemas --shared
uv run python manage.py bootstrap_tenants --tenant-domain demo.localhost --tenant-name Demo --schema-name demo
uv run python manage.py migrate_schemas
uv run python manage.py tenant_command createsuperuser --schema=demo
uv run python manage.py tenant_command load_sample_questionnaire --schema=demo --user=<username>
uv run python manage.py runserver
```

Then open `http://demo.localhost:8000/`. Set `DEBUG=True` in `.env` for local development; outside DEBUG the app will not start without `SECRET_KEY`. Configure Redis and start a Celery worker when using background exports or other queued jobs. The GraphQL endpoint is available at `/graphql/` when the application is running.

## Data and integrations

This repository contains no respondent data, credentials or reference documents. It ships one original [sample questionnaire](samples/README.md); each deployment supplies its own approved questionnaires and data. It does not include the Pro-WEAI or Pro-WEAI+MI questionnaires, whose rights belong to their publishers.

| Service | Used for | Required? | Open alternative |
|---|---|---|---|
| Any OpenAI-compatible LLM endpoint (`OPENAI_API_BASE_URL`, `OPENAI_API_KEY`) | Survey translation | No | A self-hosted server such as Ollama or vLLM |
| Anthropic API (`ANTHROPIC_API_KEY`) | Generated insights | No | None yet; the feature is unavailable without a key |
| Maps.guru (`MAPS_GURU_API_KEY`) | Base map tiles in reports | No, used only if a key is set | OpenStreetMap tiles are the default |
| Sentry (`SENTRY_DSN`) | Error monitoring | No, off without a DSN | Self-hosted Sentry or GlitchTip |
| Metabase | Analytics | No | It is open source; see `compose.yaml` |

## Accounts

Self-registration is off by default. Set `ALLOW_SIGNUP=True` to turn it on; otherwise administrators create accounts.

## Data export

Administrators export submissions and reports as XLSX and CSV from the survey pages, and supported clients read data through the GraphQL API at `/graphql/`.

## Development

Use Django's management commands through `uv run python manage.py`. For example, run `uv run python manage.py check` to check the project configuration. Review the deployment settings and external service configuration before running a production installation.

## Community

See the [overview](docs/overview.md), [privacy notice](PRIVACY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md) and the [content policy](docs/content-policy.md).

## Copyright and licence

Copyright (C) 2025-2026 IntelleCap Advisory Services Pvt. Ltd. Licensed under the GNU Affero General Public License v3.0 only; see [LICENSE](LICENSE) and [NOTICE](NOTICE). The sample questionnaire and documentation are licensed under CC BY 4.0.
