from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Client(TenantMixin):
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_on = models.DateField(auto_now_add=True)

    auto_create_schema = True

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.schema_name})"


class Domain(DomainMixin):
    class Meta:
        ordering = ["domain"]
