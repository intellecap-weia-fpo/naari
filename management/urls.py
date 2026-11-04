from django.urls import path
from . import views

app_name = "management"

urlpatterns = [
    path("", views.manage_dashboard, name="dashboard"),
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<int:user_id>/", views.user_detail, name="user_detail"),
    path("users/<int:user_id>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:user_id>/upload-profile/", views.upload_profile_image, name="upload_profile_image"),
    path("users/<int:user_id>/upload-cover/", views.upload_cover_image, name="upload_cover_image"),
    path("settings/", views.settings_view, name="settings"),
]
