# Sample questionnaire

`sample_questionnaire.json` is an original demonstration questionnaire. It lets you try NAARI on a fresh installation. It is not a validated measurement instrument and is not derived from any published index.

Load it into a tenant:

```bash
uv run python manage.py tenant_command load_sample_questionnaire --schema=demo --user=<username>
```

The files in this folder are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
