from django.core.management.base import BaseCommand
from django_tenants.utils import get_public_schema_name

from customers.models import Client, Domain


class Command(BaseCommand):
    help = "Create the public tenant/domain and the first application tenant/domain."

    def add_arguments(self, parser):
        parser.add_argument("--public-domain", default="main.localhost")
        parser.add_argument("--public-name", default="Main")
        parser.add_argument("--tenant-domain", default="weai.localhost")
        parser.add_argument("--tenant-name", default="WEAI")
        parser.add_argument("--schema-name", default="weai")

    def handle(self, *args, **options):
        public_schema = get_public_schema_name()
        public_tenant, _ = Client.objects.get_or_create(
            schema_name=public_schema,
            defaults={"name": options["public_name"], "is_active": True},
        )
        Domain.objects.update_or_create(
            domain=options["public_domain"],
            defaults={"tenant": public_tenant, "is_primary": True},
        )

        tenant, _ = Client.objects.get_or_create(
            schema_name=options["schema_name"],
            defaults={"name": options["tenant_name"], "is_active": True},
        )
        Domain.objects.update_or_create(
            domain=options["tenant_domain"],
            defaults={"tenant": tenant, "is_primary": True},
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Tenants ready: "
                f"{options['public_domain']} -> {public_tenant.schema_name}, "
                f"{options['tenant_domain']} -> {tenant.schema_name}"
            )
        )
