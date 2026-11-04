import graphene
from django_tenants.utils import get_public_schema_name

from .models import Client


def tenant_domain(tenant):
    primary_domain = tenant.domains.filter(is_primary=True).first()
    if primary_domain:
        return primary_domain.domain

    domain = tenant.domains.first()
    return domain.domain if domain else ""


def tenant_api_base_url(info, tenant):
    domain = tenant_domain(tenant)
    if not domain:
        return ""

    scheme = "https" if info.context.is_secure() else "http"
    request_host = info.context.get_host()
    if ":" in request_host and ":" not in domain:
        domain = f"{domain}:{info.context.get_port()}"

    return f"{scheme}://{domain}"


class TenantType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    schema_name = graphene.String()
    domain = graphene.String()
    api_base_url = graphene.String()
    graphql_url = graphene.String()
    is_active = graphene.Boolean()

    def resolve_domain(self, info):
        return tenant_domain(self)

    def resolve_api_base_url(self, info):
        return tenant_api_base_url(info, self)

    def resolve_graphql_url(self, info):
        api_base_url = tenant_api_base_url(info, self)
        return f"{api_base_url}/graphql/" if api_base_url else ""


class SelectTenant(graphene.Mutation):
    class Arguments:
        schema_name = graphene.String(required=True)

    success = graphene.Boolean()
    message = graphene.String()
    tenant = graphene.Field(TenantType)

    def mutate(self, info, schema_name):
        tenant = (
            Client.objects.prefetch_related("domains")
            .filter(schema_name=schema_name, is_active=True)
            .exclude(schema_name=get_public_schema_name())
            .first()
        )

        if not tenant:
            return SelectTenant(
                success=False,
                message="Tenant is not available.",
                tenant=None,
            )

        if not tenant_domain(tenant):
            return SelectTenant(
                success=False,
                message="Tenant does not have a domain configured.",
                tenant=None,
            )

        return SelectTenant(success=True, message="Tenant selected.", tenant=tenant)


class TenantDiscoveryQuery(graphene.ObjectType):
    tenants = graphene.List(TenantType, search=graphene.String())
    tenant = graphene.Field(TenantType, schema_name=graphene.String(required=True))

    def resolve_tenants(self, info, search=None):
        queryset = (
            Client.objects.prefetch_related("domains")
            .filter(is_active=True)
            .exclude(schema_name=get_public_schema_name())
        )

        if search:
            queryset = queryset.filter(name__icontains=search)

        return queryset

    def resolve_tenant(self, info, schema_name):
        return (
            Client.objects.prefetch_related("domains")
            .filter(schema_name=schema_name, is_active=True)
            .exclude(schema_name=get_public_schema_name())
            .first()
        )


class TenantDiscoveryMutation(graphene.ObjectType):
    select_tenant = SelectTenant.Field()
