from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.decorators.csrf import csrf_exempt
from graphene_django.views import GraphQLView
from django.conf.urls.i18n import i18n_patterns
from django.shortcuts import redirect
from management.views import settings_view as account_settings_view

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("admin/", admin.site.urls),
    path("auth/", include("auth_app.urls")),
    path("manage/", include("management.urls")),
    path("survey/", include("survey.urls")),
    # Temporarily commented for migration cleanup
    # path("", include("cohort.urls")),
    # path("fpo/", include("fpo.urls")),
    path("", lambda request: redirect('/survey/home/')),
    path("graphql/", csrf_exempt(GraphQLView.as_view(graphiql=settings.DEBUG))),
    path("neo/", include("neo.urls")),
    path("settings/", account_settings_view, name="root_settings"),
]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
