from django.contrib import admin
from solo.admin import SingletonModelAdmin

from .models import SiteConfiguration


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(SingletonModelAdmin):
    fieldsets = (
        ('General Settings', {
            'fields': ('site_name', 'maintenance_mode', 'company_name', 'company_logo', 'favicon', 'timezone', 'date_format', 'time_format')
        }),
        ('Landing Page Settings', {
            'fields': ('landing_page_url', 'redirect_authenticated_users'),
            'description': 'Configure custom landing page redirection after login. Examples: "/", "/survey/", "https://example.com"'
        }),
        ('Apps Settings', {
            'fields': ('enabled_apps',),
            'description': 'Control which applications are visible to users. Leave empty to show all apps. Format: ["Admin", "Survey", "Cohort"]'
        }),
        ('Login Page Settings', {
            'fields': ('login_background_image', 'login_logo', 'login_page_title', 'login_welcome_message')
        }),
        ('Theme Settings', {
            'fields': ('primary_color', 'secondary_color', 'theme_mode')
        }),
        ('SMTP Settings', {
            'fields': ('smtp_host', 'smtp_port', 'smtp_username', 'smtp_password', 'smtp_use_tls', 'smtp_use_ssl', 'email_from'),
            'classes': ('collapse',)
        }),
    )
