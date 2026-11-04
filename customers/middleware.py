class MobileTenantHeaderMiddleware:
    """
    Allow mobile clients to connect through an IP address while still resolving
    the django-tenants schema by tenant domain.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_domain = request.META.get("HTTP_X_TENANT_DOMAIN")
        if tenant_domain:
            request.META["HTTP_HOST"] = tenant_domain
            request.META["SERVER_NAME"] = tenant_domain.split(":", 1)[0]

        return self.get_response(request)
