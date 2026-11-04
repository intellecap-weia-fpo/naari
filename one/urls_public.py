from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path
from django.views.decorators.csrf import csrf_exempt
from graphene_django.views import GraphQLView

from .public_schema import schema as public_graphql_schema


def tenant_app_redirect(request, path=""):
    scheme = "https" if request.is_secure() else "http"
    query = f"?{request.META['QUERY_STRING']}" if request.META.get("QUERY_STRING") else ""
    path = f"/{path}" if path else "/"
    return redirect(f"{scheme}://{settings.DEFAULT_TENANT_DOMAIN}{path}{query}")


urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "graphql/",
        csrf_exempt(GraphQLView.as_view(graphiql=settings.DEBUG, schema=public_graphql_schema)),
    ),
    path("tenants/", include("customers.urls")),
    path("auth/sign-in/", tenant_app_redirect, {"path": "auth/sign-in/"}),
    path("survey/", tenant_app_redirect, {"path": "survey/"}),
    path("survey/<path:path>", tenant_app_redirect),
    path("", lambda request: redirect("/tenants/")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
