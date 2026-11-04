from django.urls import path

from . import views

app_name = "neo"

urlpatterns = [
    path("", views.home, name="home"),
    path("set-language/", views.set_language, name="set_language"),
    path("debug-language/", views.debug_language, name="debug_language"),
]
