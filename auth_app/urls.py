from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "auth_app"

urlpatterns = [
    path("sign-in/", views.sign_in, name="sign_in"),
    path("sign-up/", views.sign_up, name="sign_up"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_redirect_view, name="profile"),
    
    # Email verification URLs
    path("send-verification/", views.send_verification_email_view, name="send_verification"),
    path("verify-email/<uuid:token>/", views.verify_email_view, name="verify_email"),
    
    # Password reset URLs
    path("password-reset/", 
         auth_views.PasswordResetView.as_view(
             template_name="auth/password_reset_form.html",
             email_template_name="auth/password_reset_email.txt",
             html_email_template_name="auth/password_reset_email.html",
             success_url="/auth/password-reset/done/"
         ),
         name="password_reset"),
    
    path("password-reset/done/",
         auth_views.PasswordResetDoneView.as_view(
             template_name="auth/password_reset_done.html"
         ),
         name="password_reset_done"),
    
    path("password-reset-confirm/<uidb64>/<token>/",
         auth_views.PasswordResetConfirmView.as_view(
             template_name="auth/password_reset_confirm.html",
             success_url="/auth/password-reset/complete/"
         ),
         name="password_reset_confirm"),
    
    path("password-reset/complete/",
         auth_views.PasswordResetCompleteView.as_view(
             template_name="auth/password_reset_complete.html"
         ),
         name="password_reset_complete"),
    
    # User Management URLs (Admin only)
    path("users/", views.user_list_view, name="user_list"),
    path("users/<int:user_id>/", views.user_detail_view, name="user_detail"),
    path("users/add/", views.user_add_view, name="user_add"),
    path("users/<int:user_id>/edit/", views.user_edit_view, name="user_edit"),
]
