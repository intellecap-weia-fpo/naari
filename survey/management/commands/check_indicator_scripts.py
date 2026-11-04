from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, tenant_context

from survey.indicator_sandbox import UnsafeScriptError, validate
from survey.models import IndicatorScript


class Command(BaseCommand):
    help = "List indicator scripts that the sandbox would refuse to run (run before deploying the sandbox)."

    def handle(self, *args, **options):
        bad = 0
        for tenant in get_tenant_model().objects.exclude(schema_name="public"):
            with tenant_context(tenant):
                for script in IndicatorScript.objects.all():
                    try:
                        validate(script.code)
                    except (SyntaxError, UnsafeScriptError) as e:
                        bad += 1
                        self.stdout.write(f"{tenant.schema_name}: script {script.pk} '{script.name}': {e}")
        self.stdout.write(self.style.SUCCESS("All scripts pass.") if not bad else self.style.WARNING(f"{bad} script(s) need changes."))
